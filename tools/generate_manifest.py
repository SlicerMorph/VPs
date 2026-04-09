#!/usr/bin/env python3
"""
Generate manifest.json and README.md gallery from presets/ folder.

Usage: run in repo root. In CI, set env GITHUB_REPOSITORY (owner/repo) and
GITHUB_REF (refs/heads/<branch>) to compute raw URLs. Defaults to
SlicerMorph/VPs and main.
"""
import os
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / 'presets'
OUT_MANIFEST = ROOT / 'manifest.json'
OUT_README = ROOT / 'README.md'

GITHUB_REPOSITORY = os.environ.get('GITHUB_REPOSITORY', 'SlicerMorph/VPs')
GITHUB_REF = os.environ.get('GITHUB_REF', 'refs/heads/main')
branch = GITHUB_REF.split('/')[-1]

RAW_BASE = f'https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{branch}/presets'

JSON_RE = re.compile(r'^([A-Za-z0-9_-]+)(?:\.vp)?\.json$', re.IGNORECASE)
PNG_RE = re.compile(r'^([A-Za-z0-9_-]+)\.png$', re.IGNORECASE)


def scan_presets():
    files = list(PRESETS.iterdir()) if PRESETS.exists() else []
    js = {}
    pngs = {}
    for f in files:
        if not f.is_file():
            continue
        name = f.name
        m = JSON_RE.match(name)
        if m:
            js[m.group(1)] = name
            continue
        m2 = PNG_RE.match(name)
        if m2:
            pngs[m2.group(1)] = name
    # build entries for prefixes present in both
    prefixes = sorted(set(js.keys()) & set(pngs.keys()), key=lambda s: s.lower())
    entries = []
    for p in prefixes:
        jname = js[p]
        pname = pngs[p]
        json_path = PRESETS / jname
        png_path = PRESETS / pname
        author = None
        description = None
        try:
            with open(json_path, 'r') as fh:
                data = json.load(fh)
            # heuristics: look for 'author' or 'name' or 'description' keys
            author = data.get('author') if isinstance(data, dict) else None
            description = data.get('description') if isinstance(data, dict) else None
        except Exception:
            pass
        # determine contributor from git history / PR metadata (best-effort)
        contributor = None

        def _git_author_for(path):
            try:
                cmd = ['git', '-C', str(ROOT), 'log', '-1', '--pretty=format:%an%x01%ae', '--', str(path)]
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
                if not out:
                    return None
                name, email = out.split('\x01', 1)
                # Try to extract GitHub username from users.noreply.github.com emails
                m = re.search(r"(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com", email)
                if m:
                    return '@' + m.group(1)
                # fallback to email localpart or name
                if '@' in email:
                    return email.split('@', 1)[0]
                return name
            except Exception:
                return None

        def _commit_sha_for(path):
            try:
                cmd = ['git', '-C', str(ROOT), 'log', '-1', '--pretty=format:%H', '--', str(path)]
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
                return out or None
            except Exception:
                return None

        def _pr_author_for_commit(sha):
            try:
                if not sha:
                    return None
                repo = GITHUB_REPOSITORY
                token = os.environ.get('GITHUB_TOKEN')
                url = f'https://api.github.com/repos/{repo}/commits/{sha}/pulls'
                headers = {
                    'Accept': 'application/vnd.github.groot-preview+json'
                }
                if token:
                    headers['Authorization'] = f'token {token}'
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read()
                    data = json.loads(body)
                    if isinstance(data, list) and len(data) > 0:
                        pr = data[0]
                        user = pr.get('user') or {}
                        login = user.get('login')
                        if login:
                            return '@' + login
                return None
            except Exception:
                # fallback: try using `gh api` (if gh CLI is authenticated)
                try:
                    cmd = ['gh', 'api', f'repos/{GITHUB_REPOSITORY}/commits/{sha}/pulls', '--jq', '.[0].user.login']
                    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
                    if out:
                        return '@' + out
                except Exception:
                    return None
                return None

        # Determine contributor: prefer JSON author field (user-supplied via Slicer module),
        # then fall back to PR author (GitHub username), then git author.
        sha = _commit_sha_for(json_path) or _commit_sha_for(png_path)
        pr_author = _pr_author_for_commit(sha)
        if author:
            contributor = author
        elif pr_author:
            contributor = pr_author
        else:
            contributor = _git_author_for(json_path) or _git_author_for(png_path)
        entry = {
            'prefix': p,
            'json_filename': jname,
            'png_filename': pname,
            'json_raw_url': f'{RAW_BASE}/{jname}',
            'png_raw_url': f'{RAW_BASE}/{pname}',
            'author': author,
            'contributor': contributor,
            'description': description,
        }
        entries.append(entry)
    return entries


def render_readme(entries):
    hdr = "# VPs — SlicerMorph Volume Property Presets\n\n"
    hdr += "Browse presets below. Click the thumbnail to open the preview image, or click ‘Download JSON’ to fetch the raw preset for importing into Slicer.\n\n"
    hdr += "Generated manifest: `manifest.json`.\n\n"
    # grid of thumbnails: 4 columns
    md = hdr
    if not entries:
        md += 'No presets found in presets/.\n'
        return md
    cols = 4
    md += '<table><tr>'
    for i, e in enumerate(entries):
        if i % cols == 0 and i != 0:
            md += '</tr><tr>'
        thumb = e['png_raw_url']
        jsonurl = e['json_raw_url']
        title = e['prefix']
        author = e.get('author') or ''
        contributor = e.get('contributor') or ''
        desc = e.get('description') or ''
        md += f"<td align=\"center\" style=\"padding:8px\">"
        md += f"<a href=\"{thumb}\" target=\"_blank\"><img src=\"{thumb}\" alt=\"{title}\" width=200></a><br/>"
        md += f"<strong>{title}</strong><br/>"
        # Prefer showing the Git contributor (GitHub username if available), fall back to JSON author
        shown = contributor or author
        if shown:
            if isinstance(shown, str) and shown.startswith('@'):
                user = shown.lstrip('@')
                md += f"<a href=\"https://github.com/{user}\">{shown}</a><br/>"
            else:
                # escape HTML-sensitive characters in plain text
                safe = str(shown).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                md += f"{safe}<br/>"
        md += f"<a href=\"{jsonurl}\">Download JSON</a>"
        md += f"</td>"
    md += '</tr></table>\n'
    return md


def main():
    entries = scan_presets()
    manifest = {'presets': entries}
    # read existing manifest if exists
    old_manifest = None
    if OUT_MANIFEST.exists():
        try:
            old_manifest = json.loads(OUT_MANIFEST.read_text())
        except Exception:
            old_manifest = None
    # write manifest if changed
    changed = False
    if old_manifest != manifest:
        OUT_MANIFEST.write_text(json.dumps(manifest, indent=2))
        changed = True
    # render README
    new_readme = render_readme(entries)
    old_readme = OUT_README.read_text() if OUT_README.exists() else None
    if old_readme != new_readme:
        OUT_README.write_text(new_readme)
        changed = True
    if changed:
        print('Manifest/README updated')
    else:
        print('No changes')
    return 0

if __name__ == '__main__':
    main()
