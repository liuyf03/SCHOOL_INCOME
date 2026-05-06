"""NCES School Attendance Boundary Survey (SABS) downloader.

Downloads the national SABS shapefile (~580 MB), filters to Washington
(STATEFP=53), and writes a compact GeoPackage to ``RAW_FILES["sabs"]``.

NCES tends to drop or stall the connection on slow streamed downloads of
the full archive. To make this robust:

- We send a browser User-Agent and configure urllib3 retries on transient
  errors (some NCES proxies serve 429/503 to non-browser clients).
- A short per-read timeout fails fast on stalled chunks instead of hanging.
- ``RAW_DIR / "SABS_1516.zip"`` is honored as a manual escape hatch — if
  you download the archive yourself in a browser and drop it there, we
  skip the network entirely and just convert the local file.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pipeline.io import RAW_DIR, RAW_FILES, STATE_FIPS

SABS_URL = "https://nces.ed.gov/programs/edge/data/SABS_1516.zip"
LOCAL_ARCHIVE: Path = RAW_DIR / "SABS_1516.zip"

CHUNK_BYTES = 1 << 20  # 1 MB
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 120  # any single 1 MB chunk should arrive well within 2 minutes
MAX_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def filter_to_wa(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter a SABS GeoDataFrame to Washington (STATEFP == STATE_FIPS)."""
    if "STATEFP" not in gdf.columns:
        raise ValueError("SABS GeoDataFrame missing STATEFP column")
    return gdf[gdf["STATEFP"] == STATE_FIPS].copy()


def _session() -> requests.Session:
    """Session with browser UA and urllib3 retries for transient errors."""
    s = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _stream_to_disk(url: str, dest: Path) -> None:
    with _session() as session:
        with session.get(
            url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
        ) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                    if chunk:
                        f.write(chunk)


def _convert_zip_to_gpkg(zip_path: Path, out: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        shp_name = next((n for n in zf.namelist() if n.endswith(".shp")), None)
    if shp_name is None:
        raise RuntimeError(f"No .shp inside {zip_path}")
    gdf = gpd.read_file(f"zip://{zip_path}!{shp_name}")
    filter_to_wa(gdf).to_file(out, driver="GPKG")


def fetch(force: bool = False) -> list[tuple[str, str, Path]]:
    out = RAW_FILES["sabs"]
    if out.exists() and not force:
        return [("SABS", "skipped", out)]

    out.parent.mkdir(parents=True, exist_ok=True)

    # Manual escape hatch: a pre-downloaded archive at LOCAL_ARCHIVE bypasses
    # the network entirely. Useful when NCES throttles the streamed download.
    if LOCAL_ARCHIVE.exists():
        _convert_zip_to_gpkg(LOCAL_ARCHIVE, out)
        return [("SABS", "converted-local", out)]

    zip_path = out.with_suffix(".tmp.zip")
    try:
        _stream_to_disk(SABS_URL, zip_path)
        _convert_zip_to_gpkg(zip_path, out)
    finally:
        zip_path.unlink(missing_ok=True)

    return [("SABS", "downloaded", out)]
