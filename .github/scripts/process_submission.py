#!/usr/bin/env python3
"""
Ingest a VRPresetHub submission posted as a GitHub issue.

Flow:
  1. Slicer module uploads <uuid>.vp.json and <uuid>.png to vp-staging S3.
  2. It opens an issue titled "New preset: <name>" with body "staging: <uuid>".
  3. This script runs from the workflow:
        - downloads the staged files
        - validates + normalizes the JSON via tools/preset_validation.py
        - sanitizes + re-encodes the PNG
        - regenerates manifest.json (so the PR is self-contained)
        - opens a *draft* PR on bot/preset-<uuid>
        - applies the `validated-preset` label
        - deletes the staging objects (best-effort)

Kill switch:
  Set repository variable INGEST_ENABLED to "true" to enable. Any other value
  (including unset) causes this script to exit 0 after commenting on the issue.

Required env (set by the workflow):
  ISSUE_BODY, ISSUE_NUMBER, ISSUE_TITLE, ISSUE_AUTHOR, GITHUB_REPOSITORY,
  GH_TOKEN, S3_ACCESS, S3_SECRET, INGEST_ENABLED
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Make tools/ importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import preset_validation as pv  # noqa: E402

import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402

REPO  = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
TOKEN = os.environ.get("GH_TOKEN", "")

# Bot identity (matches the GitHub App named slicermorph-vps-bot).
# App ID is taken from INGEST_APP_ID so the email is correct even if the
# App is later recreated under a different ID. Falls back to a sane default.
BOT_APP_ID = os.environ.get("INGEST_APP_ID", "3535677").strip()
BOT_SLUG   = os.environ.get("INGEST_APP_SLUG", "slicermorph-vps-bot").strip()
BOT_NAME   = f"{BOT_SLUG}[bot]"
BOT_EMAIL  = f"{BOT_APP_ID}+{BOT_SLUG}[bot]@users.noreply.github.com"

S3_ENDPOINT = "https://js2.jetstream-cloud.org:8001"
S3_BUCKET   = "vp-staging"

BOT_LABEL = "validated-preset"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _api(method: str, path: str, data=None):
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


def post_comment(issue_number, body: str) -> None:
    _api("POST", f"issues/{issue_number}/comments", data={"body": body})


def add_labels(issue_number, labels: list) -> None:
    try:
        _api("POST", f"issues/{issue_number}/labels", data={"labels": labels})
    except urllib.error.HTTPError as e:
        print(f"warn: could not add labels {labels}: {e}", file=sys.stderr)


def file_exists_in_repo(path: str) -> bool:
    try:
        _api("GET", f"contents/{path}")
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=os.environ["S3_ACCESS"],
        aws_secret_access_key=os.environ["S3_SECRET"],
        config=Config(signature_version="s3v4"),
        region_name="RegionOne",
    )


def _fail(issue_number, errors: list) -> None:
    msg = "## ❌ Preset validation failed\n\n"
    msg += "\n".join(f"- {e}" for e in errors)
    msg += (
        "\n\n*The submission was rejected. No changes were made to the "
        "repository. Please correct the issue and submit again from the "
        "VRPresetHub module.*"
    )
    post_comment(issue_number, msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    issue_body   = os.environ["ISSUE_BODY"]
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_author = os.environ.get("ISSUE_AUTHOR", "unknown")
    issue_title  = os.environ.get("ISSUE_TITLE", "")

    # ------------------------------------------------------------------
    # Kill switch (repo variable INGEST_ENABLED)
    # ------------------------------------------------------------------
    if os.environ.get("INGEST_ENABLED", "").strip().lower() != "true":
        post_comment(issue_number, (
            "⏸ Automated ingest is currently **disabled** for this repository "
            "(repo variable `INGEST_ENABLED` is not `true`). A maintainer will "
            "review this submission manually."
        ))
        print("INGEST_ENABLED is not 'true'; exiting cleanly.")
        return

    # ------------------------------------------------------------------
    # 1. Determine prefix from issue title "New preset: <name>"
    # ------------------------------------------------------------------
    title_m = re.search(r"New preset:\s*([A-Za-z0-9_-]+)", issue_title)
    if not title_m:
        _fail(issue_number, [
            "Could not determine preset name. The issue title must start with "
            "`New preset: <name>` using only letters, digits, `_`, and `-`."
        ])
        return

    try:
        prefix = pv.validate_prefix(title_m.group(1))
    except pv.ValidationError as e:
        _fail(issue_number, [str(e)])
        return

    json_filename = f"{prefix}.vp.json"
    png_filename  = f"{prefix}.png"

    # ------------------------------------------------------------------
    # 2. Extract staging UUID from issue body
    # ------------------------------------------------------------------
    staging_m = re.search(
        r"staging:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        issue_body,
        re.IGNORECASE,
    )
    if not staging_m:
        _fail(issue_number, [
            "No staging upload ID found in this issue. Submit presets using "
            "the **VRPresetHub** module in SlicerMorph; it handles the upload "
            "automatically."
        ])
        return

    uid = staging_m.group(1).lower()
    json_key = f"{uid}.vp.json"
    png_key  = f"{uid}.png"

    # ------------------------------------------------------------------
    # 3. Duplicate check (do this BEFORE downloading, to bail cheaply)
    # ------------------------------------------------------------------
    if file_exists_in_repo(f"presets/{json_filename}"):
        _fail(issue_number, [
            f"A preset named `{prefix}` already exists in the repository. "
            "Please choose a different name and resubmit."
        ])
        return

    # ------------------------------------------------------------------
    # 4. Download staged files
    # ------------------------------------------------------------------
    json_local = Path(f"/tmp/{uid}.vp.json")
    png_local  = Path(f"/tmp/{uid}.png")
    s3 = _s3_client()

    try:
        s3.download_file(S3_BUCKET, json_key, str(json_local))
    except Exception as e:
        _fail(issue_number, [
            f"Could not retrieve preset JSON from staging storage: `{e}`. "
            "The upload may have failed or expired — please resubmit."
        ])
        return

    try:
        s3.download_file(S3_BUCKET, png_key, str(png_local))
    except Exception as e:
        _fail(issue_number, [
            f"Could not retrieve preview PNG from staging storage: `{e}`. "
            "The upload may have failed or expired — please resubmit."
        ])
        return

    # ------------------------------------------------------------------
    # 5. Strict validation + normalization
    # ------------------------------------------------------------------
    errors = []
    normalized_json = None
    try:
        normalized_json = pv.validate_and_normalize_json(json_local.read_bytes())
    except pv.ValidationError as e:
        errors.append(str(e))

    sanitized_png = Path(f"/tmp/{prefix}.sanitized.png")
    original_dim = None
    try:
        original_dim = pv.sanitize_png(png_local, sanitized_png)
    except pv.ValidationError as e:
        errors.append(str(e))

    if errors:
        _fail(issue_number, errors)
        return

    # ------------------------------------------------------------------
    # 6. Stamp submitter metadata into the normalized JSON
    # ------------------------------------------------------------------
    if "author" not in normalized_json:
        normalized_json["author"] = f"@{issue_author}"

    # ------------------------------------------------------------------
    # 7. Write the normalized files into presets/
    # ------------------------------------------------------------------
    presets_dir = ROOT / "presets"
    presets_dir.mkdir(exist_ok=True)
    out_json = presets_dir / json_filename
    out_png  = presets_dir / png_filename
    pv.write_normalized_json(normalized_json, out_json)
    shutil.copy(sanitized_png, out_png)

    # ------------------------------------------------------------------
    # 8. Regenerate manifest.json so the PR is self-contained
    # ------------------------------------------------------------------
    try:
        env = os.environ.copy()
        env["GITHUB_REPOSITORY"] = REPO
        env.setdefault("GITHUB_REF", "refs/heads/main")
        subprocess.run(
            ["python3", str(ROOT / "tools" / "generate_manifest.py")],
            check=True, env=env,
        )
    except subprocess.CalledProcessError as e:
        _fail(issue_number, [
            f"Manifest regeneration failed: `{e}`. This is a bug in the "
            "automation; please notify a maintainer."
        ])
        return

    # ------------------------------------------------------------------
    # 9. Commit on bot branch and open a DRAFT PR
    # ------------------------------------------------------------------
    branch = f"bot/preset-{uid}"

    run("git", "config", "user.name",  BOT_NAME)
    run("git", "config", "user.email", BOT_EMAIL)
    # Configure remote with the App installation token so the push is
    # attributed to the App identity (and so downstream PR workflows fire).
    run(
        "git", "remote", "set-url", "origin",
        f"https://x-access-token:{TOKEN}@github.com/{REPO}.git",
    )
    run("git", "checkout", "-B", branch)

    add_paths = [
        str(out_json.relative_to(ROOT)),
        str(out_png.relative_to(ROOT)),
        "manifest.json",
    ]
    if (ROOT / "README.md").exists():
        add_paths.append("README.md")
    run("git", "add", *add_paths)
    run("git", "commit", "-m", f"feat(preset): add {prefix} (closes #{issue_number})")
    run("git", "push", "--force-with-lease", "origin", branch)

    pr_url = None
    try:
        original_dim_str = (
            f"{original_dim[0]}x{original_dim[1]}" if original_dim else "unknown"
        )
        pr = _api("POST", "pulls", data={
            "title": f"Add {prefix} preset",
            "body": (
                f"Adds the **{prefix}** volume rendering preset.\n\n"
                f"- Submitted by @{issue_author} via issue #{issue_number}\n"
                f"- Staging UUID: `{uid}`\n"
                f"- Original PNG dimensions: `{original_dim_str}`\n"
                f"- Sanitized PNG: `{pv.OUTPUT_PNG_SIZE}x{pv.OUTPUT_PNG_SIZE}`, "
                f"metadata stripped, re-encoded by trusted tooling\n"
                f"- JSON: validated, normalized, and re-emitted by the workflow\n\n"
                f"Closes #{issue_number}"
            ),
            "head": branch,
            "base": "main",
            "draft": True,
        })
        pr_url = pr["html_url"]
        pr_number = pr["number"]
        add_labels(pr_number, [BOT_LABEL])
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 422 and "already exists" in body:
            owner = REPO.split("/")[0]
            prs = _api("GET", f"pulls?head={owner}:{branch}&state=open")
            if prs:
                pr_url = prs[0]["html_url"]
                add_labels(prs[0]["number"], [BOT_LABEL])
        else:
            raise

    # ------------------------------------------------------------------
    # 10. Best-effort cleanup of staged objects
    # ------------------------------------------------------------------
    for key in (json_key, png_key):
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=key)
        except Exception as e:
            print(f"warn: could not delete staging object {key}: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 11. Comment on the issue
    # ------------------------------------------------------------------
    post_comment(
        issue_number,
        (
            f"## ✅ Preset `{prefix}` validated\n\n"
            f"| File | Status |\n|---|---|\n"
            f"| `presets/{json_filename}` | normalized & re-emitted |\n"
            f"| `presets/{png_filename}` | re-encoded "
            f"({pv.OUTPUT_PNG_SIZE}×{pv.OUTPUT_PNG_SIZE}, metadata stripped) |\n"
            f"| `manifest.json` | regenerated |\n\n"
            f"Draft pull request opened: **{pr_url or '(see Pull Requests tab)'}**\n\n"
            f"A maintainer will review and merge it. "
            f"Once merged, `{prefix}` will appear in the preset gallery."
        ),
    )
    print(f"PR created: {pr_url}")


if __name__ == "__main__":
    main()
