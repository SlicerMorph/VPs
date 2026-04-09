#!/usr/bin/env python3
"""
Simple validator for VPs presets.

Usage:
  - Local: python3 tools/validate.py --local <path/to/json> <path/to/png>
  - CI: python3 tools/validate.py --pr (reads GITHUB_EVENT_PATH)

The script validates JSON against schema/volume-property-schema-v1.0.0.json and checks PNG dimensions.
If PNG exceeds 1024px in either dimension, it resamples to fit 1024 and overwrites the file.
"""
import sys
import os
import re
import json
from PIL import Image
from jsonschema import validate, ValidationError

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCHEMA_PATH = os.path.join(ROOT, 'schema', 'volume-property-schema-v1.0.0.json')
# JSON may be either <prefix>.json or <prefix>.vp.json; PNG is <prefix>.png
JSON_RE = re.compile(r'^([A-Za-z0-9_-]+)(?:\.vp)?\.json$')
PNG_RE = re.compile(r'^([A-Za-z0-9_-]+)\.png$')
MAX_DIM = 1024


def load_schema():
    with open(SCHEMA_PATH, 'r') as fh:
        return json.load(fh)


def validate_json(path, schema):
    with open(path, 'r') as fh:
        data = json.load(fh)
    validate(instance=data, schema=schema)


def validate_png(path):
    with Image.open(path) as im:
        if im.format != 'PNG':
            raise ValueError(f"File {path} is not PNG (format={im.format})")
        w,h = im.size
        if w > MAX_DIM or h > MAX_DIM:
            # resample to fit
            ratio = min(MAX_DIM / w, MAX_DIM / h)
            new_size = (int(w*ratio), int(h*ratio))
            im = im.resize(new_size, Image.LANCZOS)
            im.save(path, format='PNG', optimize=True)
            return ('resized', new_size)
    return ('ok', (w,h))


def check_pair(json_path, png_path):
    # filename rules
    jname = os.path.basename(json_path)
    pname = os.path.basename(png_path)
    jm = JSON_RE.match(jname)
    pm = PNG_RE.match(pname)
    if not jm or not pm:
        raise ValueError('Filenames must be <prefix>.png and <prefix>.json or <prefix>.vp.json with prefix matching /^[A-Za-z0-9_-]+$/')
    jprefix = jm.group(1)
    pprefix = pm.group(1)
    if jprefix != pprefix:
        raise ValueError('JSON and PNG must share identical filename prefix (e.g. Murat.vp.json and Murat.png have prefix "Murat")')
    schema = load_schema()
    validate_json(json_path, schema)
    png_result = validate_png(png_path)
    return png_result


if __name__ == '__main__':
    if '--local' in sys.argv:
        try:
            idx = sys.argv.index('--local')
            j = sys.argv[idx+1]
            p = sys.argv[idx+2]
        except Exception:
            print('Usage: python3 tools/validate.py --local <json> <png>')
            sys.exit(2)
        try:
            res = check_pair(j,p)
            print('Validation OK:', res)
            sys.exit(0)
        except ValidationError as e:
            print('JSON Schema validation failed:', e)
            sys.exit(3)
        except Exception as e:
            print('Validation error:', e)
            sys.exit(4)
    elif '--pr' in sys.argv:
        # Minimal PR-mode: read event file for changed files
        event_path = os.environ.get('GITHUB_EVENT_PATH')
        if not event_path or not os.path.exists(event_path):
            print('GITHUB_EVENT_PATH not set or file missing')
            sys.exit(5)
        with open(event_path, 'r') as fh:
            ev = json.load(fh)
        files = []
        # try to extract changed files from pull_request event
        commits = ev.get('commits')
        if commits:
            for c in commits:
                files += c.get('added', []) + c.get('modified', [])
        # fallback to pull_request.files (not always present in event payload)
        if not files and 'pull_request' in ev:
            pr = ev['pull_request']
            # not listing files here; the workflow should provide a list
            pass
        files = list(dict.fromkeys(files))
        if len(files) != 2:
            print('PR must change exactly 2 files; found', len(files))
            sys.exit(6)
        # identify json and png
        j = next((f for f in files if f.lower().endswith('.json')), None)
        p = next((f for f in files if f.lower().endswith('.png')), None)
        if not j or not p:
            print('PR must include one .json and one .png')
            sys.exit(7)
        try:
            res = check_pair(os.path.join(ROOT, j), os.path.join(ROOT, p))
            print('PR validation OK:', res)
            sys.exit(0)
        except ValidationError as e:
            print('JSON Schema validation failed:', e)
            sys.exit(3)
        except Exception as e:
            print('Validation error:', e)
            sys.exit(4)
    else:
        print('Usage: --local or --pr')
        sys.exit(2)
