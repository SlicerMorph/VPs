#!/usr/bin/env python3
"""
Accept a preset submission: download files from issue body, create branch, commit, open PR.

Reads from environment:
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


def gh_api(method, path, data=None):
    repo = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
    token = os.environ["GH_TOKEN"]
    url = f"https://api.github.com/repos/{repo}/{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def post_comment(issue_number, body):
    repo = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")
    token = os.environ["GH_TOKEN"]
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


def run(*args):
    subprocess.run(args, check=True)


def main():
    issue_body = os.environ["ISSUE_BODY"]
    issue_number = os.environ["ISSUE_NUMBER"]
    issue_author = os.environ.get("ISSUE_AUTHOR", "unknown")
    repo = os.environ.get("GITHUB_REPOSITORY", "SlicerMorph/VPs")

    # --- Parse attachment URLs from issue body ---
    json_match = re.search(
        r'\[([A-Za-z0-9_\-. ]+\.(?:vp\.)?json)\]\((https://github\.com/user-attachments/files/[^)]+)\)',
        issue_body,
    )
    png_match = re.search(
        r'<img[^>]+src="(https://github\.com/user-attachments/assets/[^"]+)"',
        issue_body,
    )
    if not png_match:
        png_match = re.search(
            r'!\[[^\]]*\]\((https://github\.com/user-attachments/assets/[^)]+)\)',
            issue_body,
        )

    if not json_match or not png_match:
        post_comment(
            issue_number,
            "❌ Could not parse file attachments from this issue. "
            "Make sure both a `.vp.json` and a PNG image are attached to the issue body.",
        )
        sys.exit(1)

    json_filename = json_match.group(1).strip()
    json_url = json_match.group(2)
    png_url = png_match.group(1)
    prefix_match = re.match(r'^([A-Za-z0-9_-]+)', json_filename)
    prefix = prefix_match.group(1)

    # --- Download ---
    json_local = f"/tmp/{json_filename}"
    png_local = f"/tmp/{prefix}.png"
    download(json_url, json_local)
    download(png_url, png_local)

    # --- Git: create branch, copy files, commit, push ---
    branch = f"preset/add-{prefix.lower()}"
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "checkout", "-b", branch)

    shutil.copy(json_local, f"presets/{json_filename}")
    shutil.copy(png_local, f"presets/{prefix}.png")

    run("git", "add", f"presets/{json_filename}", f"presets/{prefix}.png")
    run("git", "commit", "-m", f"feat: add {prefix} preset (closes #{issue_number})")
    run("git", "push", "origin", branch)

    # --- Open PR ---
    pr_body = (
        f"Adds **{prefix}** volume rendering preset.\n\n"
        f"Submitted by @{issue_author} via issue #{issue_number}.\n\n"
        f"Closes #{issue_number}"
    )
    pr = gh_api("POST", "pulls", data={
        "title": f"Add {prefix} preset",
        "body": pr_body,
        "head": branch,
        "base": "main",
    })
    pr_url = pr["html_url"]

    post_comment(
        issue_number,
        f"✅ PR created: {pr_url}\n\n"
        f"Once merged, `{prefix}` will appear in the [preset gallery](https://github.com/{repo}).",
    )
    print(f"PR created: {pr_url}")


if __name__ == "__main__":
    main()
