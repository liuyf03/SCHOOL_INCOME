"""Phase 1 bootstrap smoke tests."""
from pathlib import Path

import pipeline
from pipeline import build_dataset, download, io, validate

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_imports():
    assert pipeline is not None
    for module in (io, download, build_dataset, validate):
        assert module is not None


def test_constants_present():
    assert io.STATE_FIPS == "53"
    assert hasattr(io, "TARGET_CRS")
    assert isinstance(io.TARGET_CRS, str)
    assert io.TARGET_CRS.startswith("EPSG:")


def test_data_dirs_exist():
    assert (REPO_ROOT / "data" / "raw").is_dir()
    assert (REPO_ROOT / "data" / "processed").is_dir()
