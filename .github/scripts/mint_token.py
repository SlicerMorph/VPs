#!/usr/bin/env python3
"""Mint a GitHub App installation access token for the current repo.

Inputs (env):
  INGEST_APP_ID            : numeric App ID
  INGEST_APP_PRIVATE_KEY   : PEM-encoded RSA private key (PKCS#1 or PKCS#8)
  GITHUB_REPOSITORY        : "owner/repo"

Output:
  - Writes `token=<installation-token>` to $GITHUB_OUTPUT
  - Emits `::add-mask::<token>` so the token is redacted in workflow logs

The installation-id is discovered via /repos/{owner}/{repo}/installation, so
the same code works for any repository the App is installed on without
hardcoding installation IDs.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

import jwt  # PyJWT[crypto]


def main() -> None:
    app_id = os.environ["INGEST_APP_ID"].strip()
    pem    = os.environ["INGEST_APP_PRIVATE_KEY"]
    repo   = os.environ["GITHUB_REPOSITORY"]

    # 1. Sign a short-lived JWT as the App
    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": app_id},
        pem,
        algorithm="RS256",
    )

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "vps-ingest-bot",
    }

    # 2. Find the installation ID for the repository
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/installation",
        headers=headers,
    )
    inst = json.loads(urllib.request.urlopen(req).read())
    inst_id = inst["id"]

    # 3. Mint an installation token (default lifetime: 1 hour)
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{inst_id}/access_tokens",
        method="POST",
        headers=headers,
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    token = resp["token"]

    # 4. Mask in workflow logs
    print(f"::add-mask::{token}")

    # 5. Emit as step output
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        print("ERROR: GITHUB_OUTPUT is not set", file=sys.stderr)
        sys.exit(1)
    with open(out, "a") as f:
        f.write(f"token={token}\n")


if __name__ == "__main__":
    main()
