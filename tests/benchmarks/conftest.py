"""Fresh producer evidence shared only by downstream calibration readers."""

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture(scope="session")
def _calibration_origin(tmp_path_factory):
    from tests.benchmarks.test_grading_probes import _build_index_db
    from tests.benchmarks.test_grading_score import _full_calibration

    # Use tracked synthetic inputs and the normal producer once per session.
    # The origin never goes to a mutating reader; only independent copies do.
    base = tmp_path_factory.mktemp("calibration-origin")
    index = _build_index_db(base)
    with TemporaryDirectory(dir=base) as directory:
        origin = Path(directory)
        _full_calibration(origin, index, False)
        yield origin


@pytest.fixture
def full_calibration(_calibration_origin, tmp_path):
    """A private, relocated 700-held-out/350-reference normal-producer export.

    Keep fresh-producer and special-cohort tests on their existing builders.
    copytree copies bytes (no hardlinks); decoding per consumer also isolates
    nested dictionaries. pytest retains failed consumers' tmp_path evidence.
    """
    root = Path(shutil.copytree(_calibration_origin, tmp_path / "calibration"))
    held = root / "held"
    (entry,) = json.loads((held / "g1.json").read_bytes())["entries"].values()
    return root / "run", held, entry, root / "criteria.yml"
