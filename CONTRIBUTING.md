# Contributing to VPs

Please follow these rules when submitting a preset via pull request:

- A PR must add exactly two files (no more, no less) under the `presets/` directory.
- Filenames rules:
	- PNG must be: `<prefix>.png` (e.g., `Murat.png`).
	- JSON may be either `<prefix>.json` or `<prefix>.vp.json` (e.g., `Murat.json` or `Murat.vp.json`).
	- The `prefix` must match the regex `^[A-Za-z0-9_-]+$` and both files must share the same `prefix` (for example `Murat.vp.json` pairs with `Murat.png`).
- The JSON must validate against the vendored schema at `schema/volume-property-schema-v1.0.0.json`.
- PNG previews should be close to 1024×1024; oversized images will be auto-resized by CI.
- Updates to an existing prefix require maintainer approval (see CODEOWNERS and branch protection).

Local validation:

```bash
python3 tools/validate.py --local presets/mypreset.json presets/mypreset.png
```

If CI modifies your PNG (resizing/optimization), the action will commit an update to your PR automatically.

License and contributor terms: by opening a PR you grant this repository the right to redistribute your preset files under the repository license. Include a short author credit in the JSON `author` field.
