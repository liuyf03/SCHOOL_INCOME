"""NCES School Attendance Boundary Survey (SABS) downloader.

Downloads the national SABS shapefile, filters to Washington (STATEFP=53),
and writes a compact GeoPackage to ``RAW_FILES["sabs"]``. The exact URL
may need updating as NCES re-publishes the dataset.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import requests

from pipeline.io import RAW_FILES, STATE_FIPS

SABS_URL = "https://nces.ed.gov/programs/edge/data/SABS_1516.zip"
HTTP_TIMEOUT = 600


def filter_to_wa(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter a SABS GeoDataFrame to Washington (STATEFP == STATE_FIPS)."""
    if "STATEFP" not in gdf.columns:
        raise ValueError("SABS GeoDataFrame missing STATEFP column")
    return gdf[gdf["STATEFP"] == STATE_FIPS].copy()


def fetch(force: bool = False) -> list[tuple[str, str, Path]]:
    out = RAW_FILES["sabs"]
    if out.exists() and not force:
        return [("SABS", "skipped", out)]

    out.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(SABS_URL, timeout=HTTP_TIMEOUT)
    response.raise_for_status()

    zip_path = out.with_suffix(".tmp.zip")
    zip_path.write_bytes(response.content)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            shp_name = next(
                (n for n in zf.namelist() if n.endswith(".shp")), None
            )
        if shp_name is None:
            raise RuntimeError(f"No .shp inside {SABS_URL}")
        gdf = gpd.read_file(f"zip://{zip_path}!{shp_name}")
        wa = filter_to_wa(gdf)
        wa.to_file(out, driver="GPKG")
    finally:
        zip_path.unlink(missing_ok=True)

    return [("SABS", "downloaded", out)]
