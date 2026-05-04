"""ACS 5-year estimates downloader via the Census API.

Pulls B19131 (family income by family type by presence of own children),
B11005 (households by presence of children), and B19013 (median household
income) at the block-group level for Washington, writing one parquet per
table into ``RAW_FILES``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

import pandas as pd
import requests

from pipeline.io import ACS_VINTAGE, RAW_FILES, STATE_FIPS

CENSUS_API_BASE = "https://api.census.gov/data"
ACS_TABLES: tuple[str, ...] = ("B19131", "B11005", "B19013")
HTTP_TIMEOUT = 300

GEO_COLS = ("state", "county", "tract", "block group")


def build_url(
    table_id: str,
    vintage: int = ACS_VINTAGE,
    state: str = STATE_FIPS,
) -> str:
    """URL-encoded Census API endpoint for ``table_id`` at block-group resolution."""
    return (
        f"{CENSUS_API_BASE}/{vintage}/acs/acs5"
        f"?get=group({table_id})"
        f"&for=block%20group:*"
        f"&in=state:{state}"
        f"&in=county:*"
    )


def _api_key() -> str:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is not set. Copy .env.example to .env at the "
            "repo root and add your key (or export it in your shell). "
            "Request a free key at https://api.census.gov/data/key_signup.html"
        )
    return key


def parse_response(rows: Sequence[Sequence[str]]) -> pd.DataFrame:
    """Convert a Census API response (``[[headers], [data], ...]``) into a
    DataFrame with a composite ``GEOID`` column."""
    if not rows:
        raise ValueError("Empty Census API response")
    headers = list(rows[0])
    df = pd.DataFrame(list(rows[1:]), columns=headers)

    missing = [c for c in GEO_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Census response missing geography columns: {missing}")

    df["GEOID"] = (
        df["state"].astype(str)
        + df["county"].astype(str)
        + df["tract"].astype(str)
        + df["block group"].astype(str)
    )

    for col in df.columns:
        if col.endswith("E") and col not in ("NAME",):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_table(
    table_id: str, force: bool = False
) -> tuple[str, str, Path]:
    out = RAW_FILES[f"acs_{table_id.lower()}"]
    label = f"ACS {table_id}"
    if out.exists() and not force:
        return (label, "skipped", out)

    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"{build_url(table_id)}&key={_api_key()}"
    response = requests.get(url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()

    df = parse_response(response.json())
    df.to_parquet(out)
    return (label, "downloaded", out)


def fetch(force: bool = False) -> list[tuple[str, str, Path]]:
    return [fetch_table(t, force=force) for t in ACS_TABLES]
