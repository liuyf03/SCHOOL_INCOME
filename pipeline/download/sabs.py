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
READ_TIMEOUT = 300  # NCES sometimes stalls for minutes mid-stream
MAX_RETRIES = 3
RESUME_ATTEMPTS = 6
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


_NUMERIC_STATE_COLS = ("STATEFP", "STATEFP15", "STATEFP10", "STATEFIPS")
_ALPHA_STATE_COLS = ("LSTATE", "lstate", "STUSPS", "stusps", "STATE", "state")
_LEAID_COLS = ("leaid", "LEAID")


def filter_to_wa(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter a SABS GeoDataFrame to Washington.

    SABS column naming has drifted across NCES releases (``STATEFP``,
    ``STATEFP15``, ``LSTATE``, ``lstate``...). When no explicit state
    column is present, the 2-character state FIPS prefix of ``leaid``
    (a 7-char district NCES ID) is used as a last resort.
    """
    for col in _NUMERIC_STATE_COLS:
        if col in gdf.columns:
            return gdf[gdf[col] == STATE_FIPS].copy()
    for col in _ALPHA_STATE_COLS:
        if col in gdf.columns:
            return gdf[gdf[col].astype(str).str.upper() == "WA"].copy()
    for col in _LEAID_COLS:
        if col in gdf.columns:
            return gdf[gdf[col].astype(str).str.startswith(STATE_FIPS)].copy()
    raise ValueError(
        "SABS GeoDataFrame has no recognized state column. "
        f"Columns present: {list(gdf.columns)}"
    )


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


def _archive_is_valid(path: Path) -> bool:
    """True iff ``path`` exists and is a fully-readable zip with a .shp."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            return any(n.endswith(".shp") for n in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _stream_to_disk(url: str, dest: Path, resume: bool = False) -> None:
    """Single streamed GET. If ``resume=True`` and ``dest`` has bytes,
    requests them via ``Range``; appends if the server replies 206 or
    truncates and writes from scratch on a 200."""
    headers: dict[str, str] = {}
    mode = "wb"
    if resume and dest.exists() and dest.stat().st_size > 0:
        headers["Range"] = f"bytes={dest.stat().st_size}-"
        mode = "ab"

    with _session() as session:
        with session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        ) as response:
            if response.status_code == 416:
                return  # range past end-of-file → already complete
            response.raise_for_status()
            # Server ignored Range and sent the whole thing — restart cleanly.
            if response.status_code == 200 and "Range" in headers:
                mode = "wb"
            with dest.open(mode) as f:
                for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                    if chunk:
                        f.write(chunk)


def _stream_with_resume(url: str, dest: Path, max_attempts: int = RESUME_ATTEMPTS) -> None:
    """Wrap ``_stream_to_disk`` in a retry loop that resumes from the
    partial file each time. Stops when an attempt makes no progress."""
    last_size = -1
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            _stream_to_disk(url, dest, resume=dest.exists())
            return
        except (requests.exceptions.RequestException, OSError) as exc:
            last_error = exc
            current_size = dest.stat().st_size if dest.exists() else 0
            if current_size <= last_size:
                raise RuntimeError(
                    f"SABS download stalled at {current_size:,} bytes "
                    f"after attempt {attempt}: {exc}"
                ) from exc
            last_size = current_size
    raise RuntimeError(
        f"SABS download failed after {max_attempts} resume attempts: {last_error}"
    )


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

    # ``force`` invalidates the cached archive too — otherwise re-downloading
    # would be impossible without manually deleting LOCAL_ARCHIVE.
    if force:
        LOCAL_ARCHIVE.unlink(missing_ok=True)

    if _archive_is_valid(LOCAL_ARCHIVE):
        status = "converted-local"
    else:
        # _stream_with_resume picks up where any prior partial download
        # left off via HTTP Range — important because NCES throttles or
        # drops connections on the 583 MB stream.
        _stream_with_resume(SABS_URL, LOCAL_ARCHIVE)
        status = "downloaded"

    _convert_zip_to_gpkg(LOCAL_ARCHIVE, out)
    return [("SABS", status, out)]
