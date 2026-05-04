"""Shared paths, constants, and I/O helpers.

Single source of truth for filesystem locations, the projected CRS used by
the interpolation pipeline, the ACS vintage, and the canonical raw-file
layout. Every other pipeline module imports from here rather than
hardcoding paths or magic strings.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
from dotenv import load_dotenv

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = REPO_ROOT / "data"
RAW_DIR: Path = DATA_DIR / "raw"
PROCESSED_DIR: Path = DATA_DIR / "processed"

# Load .env from the repo root if present. No-op when the file is missing,
# and never overrides values already in the environment — so test
# monkeypatching and explicit shell exports continue to win.
load_dotenv(REPO_ROOT / ".env")

# TODO(post-mvp): parameterize STATE_FIPS to a list to support multi-state builds.
STATE_FIPS: str = "53"
TARGET_CRS: str = "EPSG:2927"
ACS_VINTAGE: int = 2022

RAW_FILES: dict[str, Path] = {
    "sabs": RAW_DIR / "sabs_wa.gpkg",
    "block_groups": RAW_DIR / "tiger_block_groups_wa.gpkg",
    "blocks": RAW_DIR / "tiger_blocks_wa.gpkg",
    "ccd": RAW_DIR / "ccd_wa.parquet",
    "acs_b19131": RAW_DIR / "acs_b19131_wa.parquet",
    "acs_b11005": RAW_DIR / "acs_b11005_wa.parquet",
    "acs_b19013": RAW_DIR / "acs_b19013_wa.parquet",
}

PROCESSED_FILES: dict[str, Path] = {
    "schools": PROCESSED_DIR / "schools_wa.json",
    "search_index": PROCESSED_DIR / "search_index.json",
}


def read_geo(path: Path | str, target_crs: str = TARGET_CRS) -> gpd.GeoDataFrame:
    """Load a geospatial file and reproject to ``target_crs``.

    Raises ``ValueError`` if the source layer has no CRS, since silently
    assuming a CRS would corrupt every downstream area calculation.
    """
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS; cannot reproject safely")
    return gdf.to_crs(target_crs)


def write_json(obj: Any, path: Path | str) -> None:
    """Serialize ``obj`` to ``path`` as UTF-8 JSON with sorted keys.

    Sorted keys + indented output keep diffs readable when the processed
    artifacts are committed to the repo.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")


def read_json(path: Path | str) -> Any:
    """Load JSON from ``path`` (UTF-8)."""
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)
