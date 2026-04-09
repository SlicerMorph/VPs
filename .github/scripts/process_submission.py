#!/usr/bin/env python3
"""
Validate a preset submission from a GitHub issue body and, on success,
create a branch + PR automatically.

Environment variables (set by the workflow):
  ISSUE_BODY, ISSUE_NUMBER, ISSUE_TITLE, ISSUE_AUTHOR, GITHUB_REPOSITORY, GH_TOKEN
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error


REPO  = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
TOKEN = os.environ.get("GH_TOKEN", "")


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _api(method, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        data=body,
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_comment(issue_number, body):
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/issues/{issue_number}/comments",
        data=data,
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req):
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Suppress automatic redirect following so we can strip auth before CDN."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_png(url, dest):
    """Download a GitHub assets URL (user-attachments/assets) to dest.

    GitHub redirects these to a CDN. The auth token must NOT be forwarded
    to the CDN (S3 rejects it with 400), so we intercept the redirect and
    make a second request without the auth header.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SlicerMorphVPs-bot/1",
            "Authorization": f"token {TOKEN}",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req) as resp:
            with open(dest, "wb") as fh:
                fh.write(resp.read())
            return
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        cdn_url = e.headers.get("Location")

    cdn_req = urllib.request.Request(
        cdn_url,
        headers={"User-Agent": "SlicerMorphVPs-bot/1"},
    )
    with urllib.request.urlopen(cdn_req) as resp:
        with open(dest, "wb") as fh:
            fh.write(resp.read())


def run(*args):
    subprocess.run(args, check=True)


def file_exists_in_repo(path):
    try:
        _api("GET", f"contents/{path}")
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    issue_body   = os.environ["ISSUE_BODY"]
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_author = os.environ.get("ISSUE_AUTHOR", "unknown")
    issue_title  = os.environ.get("ISSUE_TITLE", "")

    errors = []

    # ------------------------------------------------------------------
    # 1. Determine prefix from issue title "New preset: <name>"
    # ------------------------------------------------------------------
    title_m = re.search(r'New preset:\s*([A-Za-z0-9_-]+)', issue_title)
    if not title_m:
        # fallback: try the body field
        title_m = re.search(r'\*\*Preset name:\*\*\s*([A-Za-z0-9_-]+)', issue_body)

    if not title_m:
        _fail(issue_number, [
            "Could not determine preset name. "
            "Make sure the issue title starts with `New preset: <name>` "
            "and the name uses only letters, digits, `_`, and `-`."
        ])
        return

    prefix        = title_m.group(1)
    json_filename = f"{prefix}.vp.json"

    # ------------------------------------------------------------------
    # 2. Parse JSON from fenced code block (render: json textarea)
    # ------------------------------------------------------------------
    json_block_m = re.search(r'```(?:json)?\s*\n([\s\S]+?)\n```', issue_body)

    # ------------------------------------------------------------------
    # 3. Parse PNG URL (embedded image from drag-drop)
    # ------------------------------------------------------------------
    png_m = re.search(
        r'<img[^>]+src="(https://github\.com/user-attachments/assets/[^"]+)"',
        issue_body,
    )
    if not png_m:
        png_m = re.search(
            r'!\[[^\]]*\]\((https://github\.com/user-attachments/assets/[^)]+)\)',
            issue_body,
        )

    if not json_block_m:
        errors.append(
            "No JSON code block found. "
            "Please paste the contents of your `.vp.json` file into the **Preset JSON** field."
        )
    if not png_m:
        errors.append(
            "No PNG screenshot found. "
            "Drag and drop your `<name>.png` file into the **Screenshot** field."
        )

    if errors:
        _fail(issue_number, errors, hint="Edit this issue to add the missing content, then save.")
        return

    json_text = json_block_m.group(1).strip()
    png_url   = png_m.group(1)

    # ------------------------------------------------------------------
    # 4. Validate JSON structure
    # ------------------------------------------------------------------
    try:
        data = json.loads(json_text)
        if not isinstance(data, dict) or "volumeProperties" not in data:
            errors.append(
                "The pasted JSON is missing the required `volumeProperties` key. "
                "Make sure you export using the SlicerMorph *SubmitVolumeRenderingPreset* module."
            )
    except json.JSONDecodeError as e:
        errors.append(f"The pasted JSON is not valid: {e}")

    if errors:
        _fail(issue_number, errors)
        return

    json_local = f"/tmp/{json_filename}"
    png_local  = f"/tmp/{prefix}.png"

    with open(json_local, "w") as fh:
        json.dump(data, fh, indent=2)

    # ------------------------------------------------------------------
    # 5. Download and validate PNG
    # ------------------------------------------------------------------
    try:
        download_png(png_url, png_local)
    except Exception as e:
        errors.append(f"Could not download PNG screenshot: {e}")
        _fail(issue_number, errors)
        return

    with open(png_local, "rb") as fh:
        if fh.read(8) != b'\x89PNG\r\n\x1a\n':
            errors.append("The attached image does not appear to be a valid PNG file.")
            _fail(issue_number, errors)
            return

    # ------------------------------------------------------------------
    # 6. Duplicate check
    # ------------------------------------------------------------------
    if file_exists_in_repo(f"presets/{json_filename}"):
        _fail(issue_number, [
            f"A preset named `{prefix}` already exists in the repository. "
            "Please choose a different name."
        ])
        return

    # ------------------------------------------------------------------
    # 7. Create branch, commit, open PR
    # ------------------------------------------------------------------
    branch = f"preset/add-{prefix.lower()}"

    run("git", "config", "user.name",  "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "checkout", "-b", branch)

    shutil.copy(json_local, f"presets/{json_filename}")
    shutil.copy(png_local,  f"presets/{prefix}.png")

    run("git", "add", f"presets/{json_filename}", f"presets/{prefix}.png")
    run("git", "commit", "-m", f"feat: add {prefix} preset (closes #{issue_number})")
    run("git", "push", "origin", branch)

    pr = _api("POST", "pulls", data={
        "title": f"Add {prefix} preset",
        "body": (
            f"Adds the **{prefix}** volume rendering preset.\n\n"
            f"Submitted by @{issue_author} via issue #{issue_number}.\n\n"
            f"Closes #{issue_number}"
        ),
        "head": branch,
        "base": "main",
    })
    pr_url = pr["html_url"]

    post_comment(
        issue_number,
        f"## ✅ Preset `{prefix}` validated — PR created\n\n"
        f"| File | Status |\n|---|---|\n"
        f"| `{json_filename}` | ✅ Valid |\n"
        f"| `{prefix}.png` | ✅ Valid PNG |\n\n"
        f"A pull request has been opened automatically: **{pr_url}**\n\n"
        f"A maintainer will review and merge it. "
        f"Once merged, `{prefix}` will appear in the "
        f"[preset gallery](https://github.com/{REPO}).",
    )
    print(f"PR created: {pr_url}")


def _fail(issue_number, errors, hint=""):
    msg = "## ❌ Preset validation failed\n\n"
    msg += "\n".join(f"- {e}" for e in errors)
    if hint:
        msg += f"\n\n**How to fix:** {hint}"
    post_comment(issue_number, msg)
    sys.exit(1)


if __name__ == "__main__":
    main()
