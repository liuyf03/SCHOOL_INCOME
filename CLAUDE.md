# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**School Income Explorer** — a static web app that estimates the income distribution of households with school-aged children living inside each public school's attendance zone in Washington state. Not enrolled families — zoned families. This distinction must be surfaced in the UI.

The full specification lives in [mvp_description.md](mvp_description.md).

## Planned repository layout

```
school-income-mvp/
  data/
    raw/              # gitignored — downloaded shapefiles + ACS pulls
    processed/        # committed — schools_wa.json, search_index.json
  pipeline/
    download.py       # fetches all raw inputs
    build_dataset.py  # spatial join + interpolation + JSON output
    validate.py       # FRL correlation + coverage report
  site/
    index.html
    app.js
    style.css
```

## Pipeline commands (once implemented)

```bash
# Download all raw data (one-time)
python pipeline/download.py

# Run the full spatial join + interpolation pipeline
python pipeline/build_dataset.py

# Validate output (FRL correlation, coverage report)
python pipeline/validate.py
```

## Frontend development

No build step. Serve `site/` directly:

```bash
# Any static server works, e.g.:
python -m http.server 8000 --directory site
```

## Tech stack

**Pipeline:** Python 3.11, GeoPandas, Shapely, `tobler` (PySAL areal interpolation), `pandas`, `census` or `requests` for Census API.

**Frontend:** vanilla JS (or Alpine.js/Preact), Chart.js for income histograms, Fuse.js or MiniSearch for client-side fuzzy search.

**Hosting:** GitHub Pages — no server, no database, no auth.

## Architecture: how the data flows

1. **Raw inputs** (all WA state, FIPS `53`):
   - NCES SABS shapefile → attendance-zone polygons
   - TIGER/Line block groups → ACS data containers
   - TIGER/Line census blocks → population-weighting layer for interpolation
   - ACS 5-year tables `B19131` (family income by presence of children), `B11005` (households with children), `B19013` (median income)
   - NCES CCD school directory → school names, grades, addresses, FRL counts

2. **Pipeline** (`build_dataset.py`): loads layers into a common CRS (EPSG:2927 or EPSG:3857), spatially joins attendance zones to intersecting block groups, uses `tobler.area_weighted.area_interpolate` with census blocks as the auxiliary population weight to disaggregate block-group ACS counts into school zones, computes per-school summary stats (median income, share under $35k, share over $150k, bracket histogram), joins CCD metadata.

3. **Outputs**:
   - `data/processed/schools_wa.json` — full per-school records keyed by `NCESSCH` (~2,400 schools, target <5 MB)
   - `data/processed/search_index.json` — slim `{nces_id, name, district, city}` for autocomplete

4. **Frontend**: fetches `search_index.json` on load, builds fuzzy-search index, lazy-loads per-school records from `schools_wa.json` on selection, renders Chart.js income histogram with WA state median comparison line.

## Key constraints

- Schools with missing SABS polygons or interpolated household count <50 are flagged "low confidence" — do not hide them.
- The UI must show a persistent disclaimer that figures are zoned estimates, not enrolled families.
- `data/raw/` must be gitignored (shapefiles are large).
- `data/processed/` outputs are committed (small, needed for the static site).

## Validation targets

- Pearson r between "share of families with children under $35k" and FRL eligibility rate should be >0.6. Near zero means the spatial join is broken.
- Spot-check: Seattle (urban), Bellevue (suburban), eastern WA (rural), a tribal-area school, Mercer Island (known wealth).
