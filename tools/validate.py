#!/usr/bin/env python3
"""
Validator used in two contexts:

  - Local: python3 tools/validate.py --local <path/to/json> <path/to/png>
  - CI:    python3 tools/validate.py --pr (reads $CHANGED_FILES, one path per line)

Behavior:
  * Strictly validates the JSON via tools/preset_validation.py.
  * Sanitizes the PNG in-place (re-encoded to OUTPUT_PNG_SIZE, metadata
    stripped). On CI this means the PR may contain a slightly different PNG
    than the one originally submitted -- which is the point.
  * Filenames must be `<prefix>.vp.json` and `<prefix>.png` with matching
    prefix.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import preset_validation as pv  # noqa: E402

JSON_RE = re.compile(r"^([A-Za-z0-9_-]+)(?:\.vp)?\.json$")
PNG_RE  = re.compile(r"^([A-Za-z0-9_-]+)\.png$")


def _safe_preset_path(path: Path) -> Path | None:
    candidate = path if path.is_absolute() else ROOT / path
    try:
        presets_root = (ROOT / "presets").resolve()
        candidate = candidate.resolve(strict=False)
        relative_path = candidate.relative_to(presets_root)
    except ValueError:
        return None
    return presets_root / relative_path


def check_pair(json_path: Path, png_path: Path) -> tuple:
    jname = json_path.name
    pname = png_path.name
    jm = JSON_RE.match(jname)
    pm = PNG_RE.match(pname)
    if not jm or not pm:
        raise ValueError(
            "Filenames must be <prefix>.vp.json and <prefix>.png with prefix "
            "matching /^[A-Za-z0-9_-]+$/"
        )
    if jm.group(1) != pm.group(1):
        raise ValueError(
            "JSON and PNG must share identical filename prefix "
            f"(got {jm.group(1)!r} vs {pm.group(1)!r})"
        )

    pv.validate_prefix(jm.group(1))

    raw = json_path.read_bytes()
    normalized = pv.validate_and_normalize_json(raw)

    # Re-emit JSON deterministically.
    pv.write_normalized_json(normalized, json_path)

    # Sanitize PNG in place.
    original_dim = pv.sanitize_png(png_path, png_path)
    return ("ok", original_dim)


def _local() -> int:
    try:
        idx = sys.argv.index("--local")
        j = Path(sys.argv[idx + 1])
        p = Path(sys.argv[idx + 2])
    except Exception:
        print("Usage: python3 tools/validate.py --local <json> <png>")
        return 2
    try:
        res = check_pair(j, p)
        print("Validation OK:", res)
        return 0
    except pv.ValidationError as e:
        print(f"Validation error: {e}")
        return 3
    except Exception as e:
        print(f"Validation error: {e}")
        return 4


def _pr() -> int:
    """
    PR mode: reads $CHANGED_FILES (one path per line) supplied by the workflow.
    Falls back to scanning everything under presets/ as a safety net.
    """
    raw = os.environ.get("CHANGED_FILES", "").strip()
    if raw:
        files = [Path(f.strip()) for f in raw.splitlines() if f.strip()]
    else:
        files = list((ROOT / "presets").glob("*"))

    # Restrict to files inside this repository's presets/ directory.
    presets_files = [p for f in files if (p := _safe_preset_path(f)) is not None]
    if not presets_files:
        print("No preset files changed; nothing to validate.")
        return 0

    json_files = [f for f in presets_files if f.name.endswith(".json")]
    png_files  = [f for f in presets_files if f.name.endswith(".png")]
    if len(json_files) != 1 or len(png_files) != 1:
        print(
            f"PR must change exactly one .vp.json and one .png under presets/; "
            f"got json={[str(f) for f in json_files]} png={[str(f) for f in png_files]}"
        )
        return 6

    j = json_files[0] if json_files[0].is_absolute() else ROOT / json_files[0]
    p = png_files[0]  if png_files[0].is_absolute()  else ROOT / png_files[0]
    try:
        res = check_pair(j, p)
        print("PR validation OK:", res)
        return 0
    except pv.ValidationError as e:
        print(f"Validation error: {e}")
        return 3
    except Exception as e:
        print(f"Validation error: {e}")
        return 4


if __name__ == "__main__":
    if "--local" in sys.argv:
        sys.exit(_local())
    if "--pr" in sys.argv:
        sys.exit(_pr())
    print("Usage: --local <json> <png>  |  --pr  (reads $CHANGED_FILES)")
    sys.exit(2)
