"""NCES Common Core of Data school directory downloader.

Downloads the public-school universe CSV, filters to Washington
(``LSTATE == "WA"``), and writes parquet to ``RAW_FILES["ccd"]``.
"""
from __future__ import annotations

import io as stdio
import zipfile
from pathlib import Path

import pandas as pd
import requests

from pipeline.io import RAW_FILES

CCD_URL = (
    "https://nces.ed.gov/ccd/data/zip/ccd_sch_029_2122_w_1a_071722.zip"
)
HTTP_TIMEOUT = 300

CCD_REQUIRED_COLUMNS: tuple[str, ...] = (
    "NCESSCH",
    "SCH_NAME",
    "LEA_NAME",
    "LSTATE",
)


def filter_to_wa(df: pd.DataFrame) -> pd.DataFrame:
    """Filter a CCD DataFrame to Washington schools.

    Validates that the required columns are present so a rename in a
    future CCD release surfaces immediately rather than silently
    producing an empty frame.
    """
    missing = set(CCD_REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CCD missing required columns: {sorted(missing)}")
    return df[df["LSTATE"] == "WA"].copy()


def fetch(force: bool = False) -> list[tuple[str, str, Path]]:
    out = RAW_FILES["ccd"]
    if out.exists() and not force:
        return [("CCD", "skipped", out)]

    out.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(CCD_URL, timeout=HTTP_TIMEOUT)
    response.raise_for_status()

    with zipfile.ZipFile(stdio.BytesIO(response.content)) as zf:
        csv_name = next(
            (n for n in zf.namelist() if n.lower().endswith(".csv")), None
        )
        if csv_name is None:
            raise RuntimeError(f"No .csv inside {CCD_URL}")
        with zf.open(csv_name) as f:
            df = pd.read_csv(
                f,
                dtype={"NCESSCH": str, "LZIP": str},
                low_memory=False,
            )

    df = filter_to_wa(df)
    df.to_parquet(out)
    return [("CCD", "downloaded", out)]
