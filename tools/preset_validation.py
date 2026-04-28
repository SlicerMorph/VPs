"""
Shared strict validation + sanitization for VRPresetHub submissions.

Implements the security model described in SlicerMorph/SlicerMorph#450:

- The user upload is treated as input only.
- Committed files are regenerated, normalized outputs.

Used by:
  - .github/scripts/process_submission.py (ingest from staging)
  - tools/validate.py (re-check on every PR)

The validators raise ValidationError with a human-readable message; the caller
is responsible for surfacing the error (issue comment, PR check failure, etc.).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Limits (per issue #450)
# ---------------------------------------------------------------------------

MAX_JSON_BYTES         = 256 * 1024          # 256 KB uploaded JSON
MAX_PNG_UPLOAD_BYTES   = 5 * 1024 * 1024     # 5 MB uploaded PNG
MAX_PNG_SANITIZED_BYTES = 1 * 1024 * 1024    # 1 MB after re-encoding
MAX_INPUT_DIMENSION    = 1024                # uploaded PNG max width/height
OUTPUT_PNG_SIZE        = 512                 # canvas the workflow re-encodes to

MAX_TF_POINTS          = 256                 # per transfer function
MAX_AUTHOR_LEN         = 200
MAX_DESCRIPTION_LEN    = 500
MAX_PREFIX_LEN         = 64

PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+$")

ALLOWED_TOP_LEVEL_KEYS = {
    "@schema",
    "volumeProperties",
    "description",
    "author",
}

# Keys allowed inside a single volumeProperty entry.
ALLOWED_VP_KEYS = {
    "effectiveRange",
    "isoSurfaceValues",
    "independentComponents",
    "interpolationType",
    "useClippedVoxelIntensity",
    "clippedVoxelIntensity",
    "scatteringAnisotropy",
    "components",
}

# Keys allowed inside a single component entry.
ALLOWED_COMPONENT_KEYS = {
    "componentWeight",
    "shade",
    "lighting",
    "disableGradientOpacity",
    "scalarOpacityUnitDistance",
    "rgbTransferFunction",
    "scalarOpacity",
    "gradientOpacity",
}

ALLOWED_LIGHTING_KEYS = {"diffuse", "ambient", "specular", "specularPower"}

ALLOWED_INTERPOLATION = {"linear", "nearest"}


class ValidationError(Exception):
    """Raised when a submission fails validation."""


# ---------------------------------------------------------------------------
# Filename / prefix
# ---------------------------------------------------------------------------

def validate_prefix(prefix: str) -> str:
    if not isinstance(prefix, str):
        raise ValidationError("Preset name must be a string.")
    if not prefix:
        raise ValidationError("Preset name is empty.")
    if len(prefix) > MAX_PREFIX_LEN:
        raise ValidationError(
            f"Preset name is too long ({len(prefix)} > {MAX_PREFIX_LEN} characters)."
        )
    if not PREFIX_RE.match(prefix):
        raise ValidationError(
            "Preset name may only contain letters, digits, `_`, and `-`."
        )
    return prefix


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _check_finite_numbers(node: Any, path: str) -> None:
    """Reject NaN, +Inf, -Inf anywhere in the structure."""
    if isinstance(node, float):
        if not math.isfinite(node):
            raise ValidationError(
                f"Numeric value at {path} is not finite (NaN or infinity is not allowed)."
            )
    elif isinstance(node, dict):
        for k, v in node.items():
            _check_finite_numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _check_finite_numbers(v, f"{path}[{i}]")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_keys(d: dict, allowed: Iterable[str], context: str) -> None:
    extras = set(d.keys()) - set(allowed)
    if extras:
        raise ValidationError(
            f"Unexpected keys in {context}: {sorted(extras)}. "
            f"Allowed keys are: {sorted(allowed)}."
        )


def _validate_points(
    points: Any,
    numeric_value_keys: tuple[str, ...],
    context: str,
    other_value_keys: tuple[str, ...] = (),
) -> list[dict]:
    """
    Validate a transfer-function point list.

    numeric_value_keys lists required point fields that must be JSON numbers
    (for example, ("y",) for piecewise functions).
    other_value_keys lists required point fields validated by callers after
    common point validation (for example, ("color",) for RGB transfer).
    Returns the normalized list (preserves order, drops unknown keys).
    """
    if not isinstance(points, list):
        raise ValidationError(f"{context}.points must be a list.")
    if len(points) == 0:
        raise ValidationError(f"{context}.points is empty.")
    if len(points) > MAX_TF_POINTS:
        raise ValidationError(
            f"{context}.points has {len(points)} entries; max is {MAX_TF_POINTS}."
        )

    normalized: list[dict] = []
    allowed_point_keys = {
        "x",
        "midpoint",
        "sharpness",
        *numeric_value_keys,
        *other_value_keys,
    }
    for i, pt in enumerate(points):
        if not isinstance(pt, dict):
            raise ValidationError(f"{context}.points[{i}] must be an object.")
        _require_keys(pt, allowed_point_keys, f"{context}.points[{i}]")
        if "x" not in pt:
            raise ValidationError(f"{context}.points[{i}] missing 'x'.")
        if not _is_number(pt["x"]):
            raise ValidationError(f"{context}.points[{i}].x must be a number.")
        for keys in (numeric_value_keys, other_value_keys):
            for vk in keys:
                if vk not in pt:
                    raise ValidationError(f"{context}.points[{i}] missing '{vk}'.")
        for vk in numeric_value_keys:
            if not _is_number(pt[vk]):
                raise ValidationError(f"{context}.points[{i}].{vk} must be a number.")
        for opt in ("midpoint", "sharpness"):
            if opt in pt and not _is_number(pt[opt]):
                raise ValidationError(
                    f"{context}.points[{i}].{opt} must be a number."
                )
        normalized.append({k: pt[k] for k in allowed_point_keys if k in pt})
    return normalized


def _validate_pwf(node: Any, context: str) -> dict:
    if not isinstance(node, dict):
        raise ValidationError(f"{context} must be an object.")
    _require_keys(node, {"type", "points"}, context)
    typ = node.get("type")
    if typ not in ("piecewiseLinearFunction", "piecewiseFunction"):
        raise ValidationError(
            f"{context}.type must be 'piecewiseLinearFunction' (got {typ!r})."
        )
    return {
        "type": "piecewiseLinearFunction",  # normalize legacy alias
        "points": _validate_points(
            node["points"], numeric_value_keys=("y",), context=context
        ),
    }


def _validate_ctf(node: Any, context: str) -> dict:
    if not isinstance(node, dict):
        raise ValidationError(f"{context} must be an object.")
    _require_keys(node, {"type", "points"}, context)
    if node.get("type") != "colorTransferFunction":
        raise ValidationError(
            f"{context}.type must be 'colorTransferFunction'."
        )
    points = _validate_points(
        node["points"],
        numeric_value_keys=(),
        context=context,
        other_value_keys=("color",),
    )
    for i, pt in enumerate(points):
        color = pt["color"]
        if not (isinstance(color, list) and len(color) == 3
                and all(_is_number(c) for c in color)):
            raise ValidationError(
                f"{context}.points[{i}].color must be a 3-element numeric array."
            )
    return {"type": "colorTransferFunction", "points": points}


def _validate_lighting(node: Any) -> dict:
    if not isinstance(node, dict):
        raise ValidationError("component.lighting must be an object.")
    _require_keys(node, ALLOWED_LIGHTING_KEYS, "component.lighting")
    out = {}
    for k in ALLOWED_LIGHTING_KEYS:
        if k not in node:
            raise ValidationError(f"component.lighting.{k} is required.")
        if not _is_number(node[k]):
            raise ValidationError(f"component.lighting.{k} must be a number.")
        out[k] = node[k]
    return out


def _validate_component(component: Any) -> dict:
    if not isinstance(component, dict):
        raise ValidationError("Each component must be an object.")
    _require_keys(component, ALLOWED_COMPONENT_KEYS, "component")

    required = {
        "shade": bool,
        "rgbTransferFunction": dict,
        "scalarOpacity": dict,
        "gradientOpacity": dict,
    }
    for k, t in required.items():
        if k not in component:
            raise ValidationError(f"component.{k} is required.")
        if not isinstance(component[k], t):
            raise ValidationError(f"component.{k} has unexpected type.")
    for k in ("componentWeight", "scalarOpacityUnitDistance"):
        if k not in component:
            raise ValidationError(f"component.{k} is required.")
        if not _is_number(component[k]):
            raise ValidationError(f"component.{k} must be a number.")

    out = {
        "componentWeight": component["componentWeight"],
        "shade": bool(component["shade"]),
        "lighting": _validate_lighting(component.get("lighting", {})),
        "disableGradientOpacity": bool(component.get("disableGradientOpacity", False)),
        "scalarOpacityUnitDistance": component["scalarOpacityUnitDistance"],
        "rgbTransferFunction": _validate_ctf(
            component["rgbTransferFunction"], "component.rgbTransferFunction"
        ),
        "scalarOpacity": _validate_pwf(
            component["scalarOpacity"], "component.scalarOpacity"
        ),
        "gradientOpacity": _validate_pwf(
            component["gradientOpacity"], "component.gradientOpacity"
        ),
    }
    return out


def _validate_volume_property(vp: Any) -> dict:
    if not isinstance(vp, dict):
        raise ValidationError("volumeProperties[0] must be an object.")
    _require_keys(vp, ALLOWED_VP_KEYS, "volumeProperties[0]")

    components = vp.get("components")
    if not isinstance(components, list) or len(components) != 1:
        raise ValidationError(
            "volumeProperties[0].components must contain exactly 1 component."
        )

    interp = vp.get("interpolationType", "linear")
    if interp not in ALLOWED_INTERPOLATION:
        raise ValidationError(
            f"volumeProperties[0].interpolationType must be one of {sorted(ALLOWED_INTERPOLATION)}."
        )

    effective_range = vp.get("effectiveRange", [0.0, 0.0])
    if (not isinstance(effective_range, list) or len(effective_range) != 2
            or not all(_is_number(x) for x in effective_range)):
        raise ValidationError(
            "volumeProperties[0].effectiveRange must be a 2-element numeric array."
        )

    iso = vp.get("isoSurfaceValues", [])
    if not isinstance(iso, list) or not all(_is_number(x) for x in iso):
        raise ValidationError(
            "volumeProperties[0].isoSurfaceValues must be an array of numbers."
        )
    if len(iso) > MAX_TF_POINTS:
        raise ValidationError(
            f"volumeProperties[0].isoSurfaceValues has too many entries ({len(iso)})."
        )
    for k in ("clippedVoxelIntensity", "scatteringAnisotropy"):
        if k in vp and not _is_number(vp[k]):
            raise ValidationError(f"volumeProperties[0].{k} must be a number.")

    out = {
        "effectiveRange": list(effective_range),
        "isoSurfaceValues": list(iso),
        "independentComponents": bool(vp.get("independentComponents", True)),
        "interpolationType": interp,
        "useClippedVoxelIntensity": bool(vp.get("useClippedVoxelIntensity", False)),
        "clippedVoxelIntensity": float(vp.get("clippedVoxelIntensity", -1e10)),
        "scatteringAnisotropy": float(vp.get("scatteringAnisotropy", 0.0)),
        "components": [_validate_component(components[0])],
    }
    return out


def validate_and_normalize_json(raw_bytes: bytes) -> dict:
    """
    Parse and strictly validate the uploaded JSON bytes.

    Returns a normalized dict suitable for re-serialization. The returned
    object contains only known keys, in a deterministic structure.
    """
    if len(raw_bytes) > MAX_JSON_BYTES:
        raise ValidationError(
            f"Preset JSON is too large ({len(raw_bytes)} bytes; max {MAX_JSON_BYTES})."
        )
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Preset JSON is not valid JSON: {e}") from None

    if not isinstance(data, dict):
        raise ValidationError("Preset JSON top-level must be an object.")

    _require_keys(data, ALLOWED_TOP_LEVEL_KEYS, "preset JSON")
    _check_finite_numbers(data, "preset")

    vps = data.get("volumeProperties")
    if not isinstance(vps, list) or len(vps) != 1:
        raise ValidationError(
            "Preset JSON must contain exactly 1 entry in volumeProperties."
        )

    normalized: dict = {
        "@schema": data.get(
            "@schema",
            "https://raw.githubusercontent.com/slicer/slicer/main/"
            "Modules/Loadable/VolumeRendering/Resources/Schema/"
            "volume-property-schema-v1.0.0.json#",
        ),
        "volumeProperties": [_validate_volume_property(vps[0])],
    }

    if "description" in data:
        desc = data["description"]
        if not isinstance(desc, str):
            raise ValidationError("description must be a string.")
        if len(desc) > MAX_DESCRIPTION_LEN:
            raise ValidationError(
                f"description is too long ({len(desc)} > {MAX_DESCRIPTION_LEN})."
            )
        if desc.strip():
            normalized["description"] = desc.strip()

    if "author" in data:
        auth = data["author"]
        if not isinstance(auth, str):
            raise ValidationError("author must be a string.")
        if len(auth) > MAX_AUTHOR_LEN:
            raise ValidationError(
                f"author is too long ({len(auth)} > {MAX_AUTHOR_LEN})."
            )
        if auth.strip():
            normalized["author"] = auth.strip()

    return normalized


def write_normalized_json(normalized: dict, path: Path) -> None:
    """Write the normalized JSON deterministically (sorted-ish, 4-space indent)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(normalized, fh, indent=4)
        fh.write("\n")


# ---------------------------------------------------------------------------
# PNG sanitization
# ---------------------------------------------------------------------------

def sanitize_png(input_path: Path, output_path: Path) -> tuple[int, int]:
    """
    Sanitize an uploaded PNG: verify, decode under bounded limits, re-encode
    to a fresh OUTPUT_PNG_SIZE × OUTPUT_PNG_SIZE PNG with metadata stripped.

    Returns the (width, height) of the original decoded image (post EXIF
    transpose) for diagnostic purposes.
    """
    from PIL import Image, ImageOps  # imported lazily so callers without Pillow can import the module

    in_size = input_path.stat().st_size
    if in_size > MAX_PNG_UPLOAD_BYTES:
        raise ValidationError(
            f"PNG upload is too large ({in_size} bytes; max {MAX_PNG_UPLOAD_BYTES})."
        )

    # First pass: verify file is structurally valid PNG. verify() exhausts the
    # stream, so we open again afterwards to actually use the pixels.
    with Image.open(input_path) as image:
        if image.format != "PNG":
            raise ValidationError(f"Preview is not a PNG (decoded as {image.format}).")
        image.verify()

    with Image.open(input_path) as image:
        if image.width <= 0 or image.height <= 0:
            raise ValidationError("PNG has invalid dimensions.")
        original_size = (image.width, image.height)

        # If the upload is unusually large in either dimension, downscale to a
        # safe working size before further processing. We do not reject for
        # being "too big" (the byte cap above is the security boundary); we
        # only refuse genuinely empty / corrupt images.
        working = image
        if working.width > MAX_INPUT_DIMENSION or working.height > MAX_INPUT_DIMENSION:
            working = working.copy()
            working.thumbnail(
                (MAX_INPUT_DIMENSION, MAX_INPUT_DIMENSION), Image.LANCZOS
            )

        # EXIF transpose is a no-op for PNGs lacking EXIF, but it normalizes
        # any orientation the decoder might have respected.
        sanitized = ImageOps.exif_transpose(working)
        sanitized = sanitized.convert("RGBA")
        sanitized.thumbnail((OUTPUT_PNG_SIZE, OUTPUT_PNG_SIZE), Image.LANCZOS)

        # Centre on a transparent canvas so output dimensions are deterministic.
        canvas = Image.new(
            "RGBA", (OUTPUT_PNG_SIZE, OUTPUT_PNG_SIZE), (0, 0, 0, 0)
        )
        x = (OUTPUT_PNG_SIZE - sanitized.width) // 2
        y = (OUTPUT_PNG_SIZE - sanitized.height) // 2
        canvas.paste(sanitized, (x, y), sanitized)

        # Do NOT pass pnginfo. This strips text/comment/EXIF/ICC chunks.
        canvas.save(output_path, format="PNG", optimize=True)

    out_size = output_path.stat().st_size
    if out_size > MAX_PNG_SANITIZED_BYTES:
        raise ValidationError(
            f"Sanitized PNG is unexpectedly large ({out_size} bytes; "
            f"max {MAX_PNG_SANITIZED_BYTES})."
        )
    return original_size
