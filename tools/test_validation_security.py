import copy
import json
import unittest
from pathlib import Path

from tools import preset_validation as pv
from tools import validate


ROOT = Path(__file__).resolve().parents[1]


class PresetValidationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.preset = json.loads((ROOT / "presets" / "Skin.vp.json").read_text())

    def _rejects_bool_at(self, path):
        data = copy.deepcopy(self.preset)
        node = data
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = True

        with self.assertRaisesRegex(pv.ValidationError, "number|numeric"):
            pv.validate_and_normalize_json(json.dumps(data).encode())

    def test_rejects_booleans_in_numeric_volume_property_fields(self):
        self._rejects_bool_at(["volumeProperties", 0, "effectiveRange", 0])
        self._rejects_bool_at(["volumeProperties", 0, "isoSurfaceValues", 0])
        self._rejects_bool_at(["volumeProperties", 0, "clippedVoxelIntensity"])

    def test_rejects_booleans_in_numeric_component_fields(self):
        component = ["volumeProperties", 0, "components", 0]
        self._rejects_bool_at(component + ["componentWeight"])
        self._rejects_bool_at(component + ["lighting", "diffuse"])
        self._rejects_bool_at(component + ["rgbTransferFunction", "points", 0, "x"])
        self._rejects_bool_at(component + ["rgbTransferFunction", "points", 0, "color", 0])
        self._rejects_bool_at(component + ["scalarOpacity", "points", 0, "y"])

    def test_pr_changed_files_are_limited_to_repo_presets_directory(self):
        self.assertEqual(
            validate._preset_path(Path("presets/Skin.vp.json")),
            (ROOT / "presets" / "Skin.vp.json").resolve(),
        )
        self.assertIsNone(validate._preset_path(Path("presets/../README.md")))
        self.assertIsNone(validate._preset_path(Path("/tmp/presets/Skin.vp.json")))


if __name__ == "__main__":
    unittest.main()
