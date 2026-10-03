"""Installed-distribution smoke test for the declared public Python surface."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicDependencySurfaceTests(unittest.TestCase):
    def test_non_editable_install_exposes_the_declared_surface(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            install_root = temporary_root / "site"
            source_root = temporary_root / "source"
            shutil.copytree(
                ROOT,
                source_root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "build", "*.egg-info"),
            )
            install = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-build-isolation",
                    "--target",
                    str(install_root),
                    ".",
                ],
                cwd=source_root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, install.returncode, install.stdout + install.stderr)

            smoke = """
import importlib.metadata
from pathlib import Path
import sys

install_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(install_root))

import quant_platform
from quant_platform.access import DataGateway, DataRequest
from quant_platform.replay import HistoricalReplayRuntime, ReplayEngine, ReplayError, ReplayResult, ReplaySpec
from quant_platform.validation import (
    ComparableTrialPanel,
    Embargo,
    EffectiveTrialCountEvidence,
    ValidationCandidate,
    classify_candidate,
    evaluate_dsr_v1,
    evaluate_pbo_v1,
)

assert Path(quant_platform.__file__).resolve().is_relative_to(install_root)
assert quant_platform.__version__ == importlib.metadata.version("quant-platform")
assert (install_root / "quant_platform" / "py.typed").is_file()
assert all((
    DataGateway,
    DataRequest,
    HistoricalReplayRuntime,
    ReplayEngine,
    ReplayError,
    ReplayResult,
    ReplaySpec,
    ComparableTrialPanel,
    Embargo,
    EffectiveTrialCountEvidence,
    ValidationCandidate,
    classify_candidate,
    evaluate_dsr_v1,
    evaluate_pbo_v1,
))
"""
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-I", "-c", smoke, str(install_root)],
                cwd=temporary_root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
