#!/usr/bin/env python3
"""
Validate a preset submission from an issue body.

Reads from environment:
  ISSUE_BODY, ISSUE_NUMBER, GITHUB_REPOSITORY, GH_TOKEN
Posts a comment on the issue with pass/fail details.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error


def post_comment(issue_number, repo, token, body):
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
        data=data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req):
        pass


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "SlicerMorphVPs-bot/1"})
    with urllib.request.urlopen(req) as resp, open(path, "wb") as fh:
        fh.write(resp.read())


def main():
    issue_body = os.environ["ISSUE_BODY"]
    issue_number = os.environ["ISSUE_NUMBER"]
    repo = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
    token = os.environ["GH_TOKEN"]

    errors = []

    # --- Parse attachments from issue body ---
    # JSON: markdown link  [name.vp.json](https://github.com/user-attachments/files/...)
    json_match = re.search(
        r'\[([A-Za-z0-9_\-. ]+\.(?:vp\.)?json)\]\((https://github\.com/user-attachments/files/[^)]+)\)',
        issue_body,
    )
    # PNG: <img ... src="https://github.com/user-attachments/assets/..." ...>
    png_match = re.search(
        r'<img[^>]+src="(https://github\.com/user-attachments/assets/[^"]+)"',
        issue_body,
    )
    # Fallback: markdown image  ![...](https://github.com/user-attachments/assets/...)
    if not png_match:
        png_match = re.search(
            r'!\[[^\]]*\]\((https://github\.com/user-attachments/assets/[^)]+)\)',
            issue_body,
        )

    if not json_match:
        errors.append(
            "No `.vp.json` file attachment found. "
            "Drag and drop the JSON file exported by SlicerMorph into the issue body."
        )
    if not png_match:
        errors.append(
            "No PNG image attachment found. "
            "Drag and drop the PNG screenshot exported by SlicerMorph into the issue body."
        )

    if errors:
        msg = "## ❌ Preset validation failed\n\n"
        msg += "\n".join(f"- {e}" for e in errors)
        msg += (
            "\n\n**How to fix:** Edit this issue, drag both exported files "
            "(`<name>.vp.json` and `<name>.png`) into the text area, and save."
        )
        post_comment(issue_number, repo, token, msg)
        sys.exit(1)

    json_filename = json_match.group(1).strip()
    json_url = json_match.group(2)
    png_url = png_match.group(1)

    # --- Prefix / filename validation ---
    prefix_match = re.match(r'^([A-Za-z0-9_-]+)(?:\.vp)?\.json$', json_filename)
    if not prefix_match:
        errors.append(
            f"`{json_filename}` does not match the required naming pattern `<name>.vp.json`. "
            "Name may only contain letters, digits, `_` and `-`."
        )

    if errors:
        msg = "## ❌ Preset validation failed\n\n"
        msg += "\n".join(f"- {e}" for e in errors)
        post_comment(issue_number, repo, token, msg)
        sys.exit(1)

    prefix = prefix_match.group(1)

    # --- Download files ---
    json_local = f"/tmp/{json_filename}"
    png_local = f"/tmp/{prefix}.png"

    try:
        download(json_url, json_local)
    except Exception as e:
        errors.append(f"Could not download JSON file: {e}")

    try:
        download(png_url, png_local)
    except Exception as e:
        errors.append(f"Could not download PNG file: {e}")

    if errors:
        msg = "## ❌ Preset validation failed\n\n" + "\n".join(f"- {e}" for e in errors)
        post_comment(issue_number, repo, token, msg)
        sys.exit(1)

    # --- Validate JSON ---
    try:
        with open(json_local) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            errors.append("JSON file must be a JSON object, not an array or scalar.")
        elif "volumeProperties" not in data:
            errors.append(
                "JSON file is missing the required `volumeProperties` key. "
                "Make sure you export from the SlicerMorph SubmitVolumeRenderingPreset module."
            )
    except json.JSONDecodeError as e:
        errors.append(f"JSON file is not valid JSON: {e}")

    # --- Validate PNG (check magic bytes) ---
    try:
        with open(png_local, "rb") as fh:
            sig = fh.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            errors.append("Attached image does not appear to be a valid PNG file.")
    except Exception as e:
        errors.append(f"Could not read PNG file: {e}")

    # --- Check for duplicate prefix ---
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/contents/presets/{json_filename}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        )
        urllib.request.urlopen(req)
        # If we get here the file already exists
        errors.append(
            f"A preset named `{prefix}` already exists in the repository. "
            "Please choose a different name."
        )
    except urllib.error.HTTPError as e:
        if e.code != 404:
            pass  # unexpected error — skip duplicate check

    if errors:
        msg = "## ❌ Preset validation failed\n\n" + "\n".join(f"- {e}" for e in errors)
        msg += "\n\nPlease fix the issues and edit/re-submit this issue."
        post_comment(issue_number, repo, token, msg)
        sys.exit(1)

    # --- All good ---
    msg = (
        f"## ✅ Preset `{prefix}` validated successfully\n\n"
        f"| File | Status |\n|---|---|\n"
        f"| `{json_filename}` | ✅ Valid JSON with `volumeProperties` |\n"
        f"| `{prefix}.png` | ✅ Valid PNG |\n\n"
        f"A maintainer will review and approve this submission by commenting `/accept`.\n\n"
        f"Once merged, your preset will appear in the "
        f"[SlicerMorph VPs gallery](https://github.com/{repo})."
    )
    post_comment(issue_number, repo, token, msg)
    print(f"Validation passed for preset: {prefix}")


if __name__ == "__main__":
    main()
