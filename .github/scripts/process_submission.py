#!/usr/bin/env python3
"""
Validate a preset submission from a GitHub issue body and, on success,
create a branch + PR automatically.

Environment variables (set by the workflow):
  ISSUE_BODY, ISSUE_NUMBER, ISSUE_AUTHOR, GITHUB_REPOSITORY, GH_TOKEN
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error


REPO = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
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


def download(url, dest):
    headers = {"User-Agent": "SlicerMorphVPs-bot/1"}
    if "github.com/user-attachments/" in url and TOKEN:
        headers["Authorization"] = f"token {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())


def run(*args):
    subprocess.run(args, check=True)


def duplicate_exists(json_filename):
    try:
        _api("GET", f"contents/presets/{json_filename}")
        return True  # 200 → file exists
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return False  # unexpected error — skip check


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    issue_body   = os.environ["ISSUE_BODY"]
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_author = os.environ.get("ISSUE_AUTHOR", "unknown")

    errors = []

    # ------------------------------------------------------------------
    # 1. Parse attachment URLs from issue body
    # ------------------------------------------------------------------
    # JSON: markdown link  [name.vp.json](https://github.com/user-attachments/files/...)
    json_match = re.search(
        r'\[([A-Za-z0-9_\-. ]+\.(?:vp\.)?json)\]'
        r'\((https://github\.com/user-attachments/files/[^)]+)\)',
        issue_body,
    )
    # PNG: HTML img tag (GitHub renders drag-dropped images this way)
    png_match = re.search(
        r'<img[^>]+src="(https://github\.com/user-attachments/assets/[^"]+)"',
        issue_body,
    )
    # Fallback: markdown image syntax
    if not png_match:
        png_match = re.search(
            r'!\[[^\]]*\]\((https://github\.com/user-attachments/assets/[^)]+)\)',
            issue_body,
        )

    if not json_match:
        errors.append(
            "No `.vp.json` file attachment found. "
            "Drag and drop the JSON file exported from SlicerMorph into the issue body."
        )
    if not png_match:
        errors.append(
            "No PNG image attachment found. "
            "Drag and drop the PNG screenshot exported from SlicerMorph into the issue body."
        )

    if errors:
        _fail(issue_number, errors, hint="Edit this issue and attach both files, then save.")
        return

    json_filename = json_match.group(1).strip()
    json_url      = json_match.group(2)
    png_url       = png_match.group(1)

    # ------------------------------------------------------------------
    # 2. Filename / prefix validation
    # ------------------------------------------------------------------
    prefix_m = re.match(r'^([A-Za-z0-9_-]+)(?:\.vp)?\.json$', json_filename)
    if not prefix_m:
        errors.append(
            f"`{json_filename}` does not follow the required naming pattern "
            "`<name>.vp.json`. Allowed characters: A-Z a-z 0-9 _ -"
        )
        _fail(issue_number, errors)
        return

    prefix = prefix_m.group(1)

    # ------------------------------------------------------------------
    # 3. Download files
    # ------------------------------------------------------------------
    json_local = f"/tmp/{json_filename}"
    png_local  = f"/tmp/{prefix}.png"

    for url, dest, label in [(json_url, json_local, "JSON"), (png_url, png_local, "PNG")]:
        try:
            download(url, dest)
        except Exception as e:
            errors.append(f"Could not download {label} file: {e}")

    if errors:
        _fail(issue_number, errors)
        return

    # ------------------------------------------------------------------
    # 4. Validate JSON structure
    # ------------------------------------------------------------------
    try:
        with open(json_local) as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "volumeProperties" not in data:
            errors.append(
                "JSON file is missing the required `volumeProperties` key. "
                "Make sure you export using the SlicerMorph *SubmitVolumeRenderingPreset* module."
            )
    except json.JSONDecodeError as e:
        errors.append(f"JSON file is not valid JSON: {e}")

    # ------------------------------------------------------------------
    # 5. Validate PNG magic bytes
    # ------------------------------------------------------------------
    try:
        with open(png_local, "rb") as fh:
            if fh.read(8) != b'\x89PNG\r\n\x1a\n':
                errors.append("Attached image does not appear to be a valid PNG file.")
    except Exception as e:
        errors.append(f"Could not read PNG file: {e}")

    # ------------------------------------------------------------------
    # 6. Duplicate check
    # ------------------------------------------------------------------
    if duplicate_exists(json_filename):
        errors.append(
            f"A preset named `{prefix}` already exists in the repository. "
            "Please choose a different name."
        )

    if errors:
        _fail(issue_number, errors, hint="Please fix the issues above and edit this issue to re-trigger validation.")
        return

    # ------------------------------------------------------------------
    # 7. All good — create branch, commit, open PR
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
