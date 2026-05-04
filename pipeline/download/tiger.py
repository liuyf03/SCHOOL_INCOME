"""TIGER/Line shapefile downloader for WA block groups and 2020 blocks."""
from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import requests

from pipeline.io import ACS_VINTAGE, RAW_FILES, STATE_FIPS

BLOCK_GROUPS_URL = (
    f"https://www2.census.gov/geo/tiger/TIGER{ACS_VINTAGE}/BG/"
    f"tl_{ACS_VINTAGE}_{STATE_FIPS}_bg.zip"
)
BLOCKS_URL = (
    f"https://www2.census.gov/geo/tiger/TIGER{ACS_VINTAGE}/TABBLOCK20/"
    f"tl_{ACS_VINTAGE}_{STATE_FIPS}_tabblock20.zip"
)
HTTP_TIMEOUT = 900


def _download_and_convert(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()

    zip_path = out_path.with_suffix(".tmp.zip")
    zip_path.write_bytes(response.content)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            shp_name = next(
                (n for n in zf.namelist() if n.endswith(".shp")), None
            )
        if shp_name is None:
            raise RuntimeError(f"No .shp inside {url}")
        gdf = gpd.read_file(f"zip://{zip_path}!{shp_name}")
        gdf.to_file(out_path, driver="GPKG")
    finally:
        zip_path.unlink(missing_ok=True)


def fetch_block_groups(force: bool = False) -> tuple[str, str, Path]:
    out = RAW_FILES["block_groups"]
    if out.exists() and not force:
        return ("TIGER block groups", "skipped", out)
    _download_and_convert(BLOCK_GROUPS_URL, out)
    return ("TIGER block groups", "downloaded", out)


def fetch_blocks(force: bool = False) -> tuple[str, str, Path]:
    out = RAW_FILES["blocks"]
    if out.exists() and not force:
        return ("TIGER blocks", "skipped", out)
    _download_and_convert(BLOCKS_URL, out)
    return ("TIGER blocks", "downloaded", out)


def fetch(force: bool = False) -> list[tuple[str, str, Path]]:
    return [fetch_block_groups(force=force), fetch_blocks(force=force)]
