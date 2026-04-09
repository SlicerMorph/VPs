#!/usr/bin/env python3
"""
Validate a preset submission from a GitHub issue and, on success,
create a branch + PR automatically.

Submission flow:
  1. Slicer module uploads <uuid>.vp.json and <uuid>.png to the vp-staging S3 bucket.
  2. It opens a GitHub issue with title "New preset: <name>" and body "staging: <uuid>".
  3. This script downloads the files from S3, validates them, creates a PR, and
     deletes the staging objects.

Environment variables (set by the workflow):
  ISSUE_BODY, ISSUE_NUMBER, ISSUE_TITLE, ISSUE_AUTHOR, GITHUB_REPOSITORY,
  GH_TOKEN, S3_ACCESS, S3_SECRET
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error

import boto3
from botocore.config import Config

REPO  = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
TOKEN = os.environ.get("GH_TOKEN", "")

S3_ENDPOINT = "https://js2.jetstream-cloud.org:8001"
S3_BUCKET   = "vp-staging"


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


def run(*args):
    subprocess.run(args, check=True)


def file_exists_in_repo(path):
    try:
        _api("GET", f"contents/{path}")
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=os.environ["S3_ACCESS"],
        aws_secret_access_key=os.environ["S3_SECRET"],
        config=Config(signature_version="s3v4"),
        region_name="RegionOne",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    issue_body   = os.environ["ISSUE_BODY"]
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_author = os.environ.get("ISSUE_AUTHOR", "unknown")
    issue_title  = os.environ.get("ISSUE_TITLE", "")

    # ------------------------------------------------------------------
    # 1. Determine prefix from issue title "New preset: <name>"
    # ------------------------------------------------------------------
    title_m = re.search(r'New preset:\s*([A-Za-z0-9_-]+)', issue_title)
    if not title_m:
        _fail(issue_number, [
            "Could not determine preset name. "
            "Make sure the issue title starts with `New preset: <name>` "
            "using only letters, digits, `_`, and `-`."
        ])
        return

    prefix        = title_m.group(1)
    json_filename = f"{prefix}.vp.json"

    # ------------------------------------------------------------------
    # 2. Extract staging UUID from issue body
    # ------------------------------------------------------------------
    staging_m = re.search(
        r'staging:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
        issue_body,
        re.IGNORECASE,
    )
    if not staging_m:
        _fail(issue_number, [
            "No staging upload ID found in this issue. "
            "Please submit presets using the **SubmitVolumeRenderingPreset** module in SlicerMorph — "
            "it handles the upload automatically."
        ])
        return

    uid      = staging_m.group(1)
    json_key = f"{uid}.vp.json"
    png_key  = f"{uid}.png"

    # ------------------------------------------------------------------
    # 3. Download files from S3 staging bucket
    # ------------------------------------------------------------------
    json_local = f"/tmp/{json_filename}"
    png_local  = f"/tmp/{prefix}.png"
    s3 = _s3_client()

    try:
        s3.download_file(S3_BUCKET, json_key, json_local)
    except Exception as e:
        _fail(issue_number, [
            f"Could not retrieve preset JSON from staging storage: `{e}`. "
            "The upload may have failed or the files may have expired — please try submitting again."
        ])
        return

    try:
        s3.download_file(S3_BUCKET, png_key, png_local)
    except Exception as e:
        _fail(issue_number, [
            f"Could not retrieve preset screenshot from staging storage: `{e}`. "
            "The upload may have failed or the files may have expired — please try submitting again."
        ])
        return

    # ------------------------------------------------------------------
    # 4. Validate JSON structure
    # ------------------------------------------------------------------
    errors = []
    try:
        with open(json_local) as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "volumeProperties" not in data:
            errors.append(
                "The preset JSON is missing the required `volumeProperties` key. "
                "Make sure you export using the SlicerMorph *SubmitVolumeRenderingPreset* module."
            )
    except json.JSONDecodeError as e:
        errors.append(f"The preset JSON is not valid: {e}")

    # ------------------------------------------------------------------
    # 5. Validate PNG
    # ------------------------------------------------------------------
    with open(png_local, "rb") as fh:
        header = fh.read(8)
    if header != b'\x89PNG\r\n\x1a\n':
        errors.append("The screenshot does not appear to be a valid PNG file.")

    if errors:
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
    # 7. Delete staging objects (best-effort — don't block on failure)
    # ------------------------------------------------------------------
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=json_key)
        s3.delete_object(Bucket=S3_BUCKET, Key=png_key)
    except Exception as e:
        print(f"Warning: could not delete staging objects: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 8. Create branch, commit, open PR
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


def _fail(issue_number, errors):
    msg = "## ❌ Preset validation failed\n\n"
    msg += "\n".join(f"- {e}" for e in errors)
    post_comment(issue_number, msg)
    sys.exit(1)


if __name__ == "__main__":
    main()
