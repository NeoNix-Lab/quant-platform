"""Regression evidence for the Golden proof/test-double boundary decision."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import wave6_golden  # noqa: E402


class Wave6GoldenTestDoubleBoundaryTests(unittest.TestCase):
    def test_wave6_test_doubles_are_private_and_not_module_exports(self):
        doubles = ("_FakeCatalog", "_FakeBatchReader", "_FakeRelocationCatalog")

        for name in doubles:
            self.assertTrue(name.startswith("_"))
            self.assertTrue(hasattr(wave6_golden, name))
            self.assertNotIn(name, wave6_golden.__all__)


if __name__ == "__main__":
    unittest.main()
