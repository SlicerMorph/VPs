# VPs — SlicerMorph Volume Property Presets

Repository of Slicer volume property presets. Each preset is a pair of files that share the same filename prefix:

- `<prefix>.json` — the volume property JSON (Slicer format)
- `<prefix>.png` — a preview image (thumbnail)

Contributions must follow the rules in `CONTRIBUTING.md`. A GitHub Action validates PRs to ensure exactly two files are submitted, filenames match, JSON validates against the vendored schema, and PNGs meet size constraints.

Quick start (download into Slicer):
1. Open the `presets/` folder in this repo on GitHub.
2. Click a `*.json` file, then `Raw` to get the raw URL.
3. In Slicer, load the JSON via Volume Rendering / Presets import.

