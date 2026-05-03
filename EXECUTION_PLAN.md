# School Income Explorer — MVP Execution Plan

## Context

The repo currently contains only a specification ([mvp_description.md](mvp_description.md)) and a project guide ([CLAUDE.md](CLAUDE.md)). This plan converts the 8-step MVP description into an execution-ordered, dependency-aware sequence of phases that can be tackled iteratively. The project is a static web app that estimates the income distribution of households with school-aged children zoned to each WA public school, built from a Python geospatial pipeline that joins NCES SABS attendance polygons, TIGER block groups/blocks, ACS 5-year tables, and NCES CCD school metadata into a single ~5 MB JSON consumed by a vanilla-JS frontend on GitHub Pages.

Every phase ships with **pytest unit tests in the same iteration** — testing is not deferred.

---

## Phase ordering at a glance

```
1 bootstrap
 ├─ 2 io layer ─┬─ 4 download ─┐
 │              └─ 5 geo core ─┴─ 6 orchestration ─ 7 validate ─ 8 real-data UI ─ 9 CI/deploy ─ 10 post-MVP docs
 └─ 3 frontend stub (parallel; locks JSON schema) ─────────────────────────────────^
```

Phase 3 runs in parallel with 2/4/5 and locks the JSON schema before any pipeline code commits to it. Phase 7 is the only hard automated gate (FRL Pearson r ≥ 0.6); manual spot-checks gate Phase 9.

---

## Phase 1 — Project bootstrap & test harness

**Goal:** Importable `school_income` package, pytest harness, directory skeleton.

**Deliverables:** `pyproject.toml` (deps: geopandas, shapely, tobler, pandas, requests, pyproj; dev: pytest, pytest-cov, responses, pytest-mock), `.gitignore` (ignores `data/raw/`, `.venv/`, caches, `.env`), `.env.example` (placeholder for `CENSUS_API_KEY`), `pipeline/__init__.py`, empty modules `pipeline/{download,build_dataset,validate,io}.py`, `tests/conftest.py`, `tests/test_smoke.py`, `pytest.ini` (markers: `integration`, `slow`), `data/raw/.gitkeep`, `data/processed/.gitkeep`, `site/`.

**Tests:** `test_package_imports`, `test_constants_present` (asserts `STATE_FIPS == "53"` and `TARGET_CRS` exist), `test_data_dirs_exist`.

**Exit:** `pip install -e ".[dev]"` succeeds, `pytest -q` green, `python -c "import pipeline"` works.

**Depends on:** none.

---

## Phase 2 — Shared I/O layer & paths

**Goal:** Centralize paths, CRS, FIPS, and JSON helpers in one place so every module imports them and tests can monkeypatch a single object.

**Deliverables:** [pipeline/io.py](pipeline/io.py) with `REPO_ROOT`, `RAW_DIR`, `PROCESSED_DIR` (pathlib), `STATE_FIPS = "53"`, `TARGET_CRS = "EPSG:2927"` (WA State Plane South — projected, area-accurate for tobler), `ACS_VINTAGE = 2022`, `RAW_FILES` dict mapping logical names to paths, `read_geo()` (load + reproject), `read_json()`/`write_json()` (UTF-8, sorted keys for diffability).

**Tests:** `test_paths_resolve_under_repo_root`, `test_target_crs_is_projected` (uses `pyproj.CRS(...).is_projected`), `test_write_then_read_json_roundtrip` (uses `tmp_path`), `test_read_geo_reprojects` (in-memory EPSG:4326 GeoDF → asserts output CRS).

**Exit:** all `tests/pipeline/test_io.py` pass; later modules import paths from here, never hardcode strings.

**Depends on:** Phase 1.

---

## Phase 3 — Frontend stub with fake data (PARALLEL)

**Goal:** Working static site against hand-authored fake JSON. Locks the schema before any pipeline code commits to it.

**Deliverables:**
- [site/index.html](site/index.html): search bar, results panel, persistent zoned-vs-enrolled disclaimer (verbatim from spec), Chart.js + MiniSearch CDN tags.
- [site/app.js](site/app.js): fetch `search_index.json` → MiniSearch index → on selection lazy-fetch from `schools_wa.json` → render histogram + summary + low-confidence badge.
- [site/style.css](site/style.css).
- `data/processed/schools_wa.json` — hand-authored fixture with 3-5 fake schools covering: normal record, missing-SABS low-confidence record, low-household-count low-confidence record.
- `data/processed/search_index.json` — matching slim records.
- [docs/schema.md](docs/schema.md) — frozen schema. Required fields: `nces_id`, `name`, `district`, `city`, `grades`, `address`, `median_family_income`, `share_under_35k`, `share_over_150k`, `total_families_with_children`, `bracket_histogram` (array of `{label, lower, upper, count}`), `low_confidence` (bool), `low_confidence_reasons` (array of `"missing_sabs"`, `"low_household_count"`).

**Tests:** `tests/site/test_fixture_schema.py` — `test_search_index_keys_match_schools`, `test_low_confidence_records_have_reasons`, `test_bracket_histogram_well_formed`, `test_required_fields_present`. **These same tests are reused unchanged in Phase 6 against the generated payload.**

**Exit:** `python -m http.server 8000 --directory site` serves a page where typing a fake school shows autocomplete, selecting renders a histogram, low-confidence school visibly displays the badge + reason.

**Depends on:** Phase 1 (test harness only). Independent of 2/4/5/6.

---

## Phase 4 — Download scripts (split per source)

**Goal:** Five raw inputs fetched reproducibly. Split per source so each is independently re-runnable, mockable, and testable; `download.py` becomes a thin orchestrator.

**Why split** (deviation from spec's "single script"): the five sources have different protocols (HTTP zip, Census API JSON, NCES portal) and failure modes. Monolithic = hard to mock, forces full re-download on partial failure.

**Deliverables:** `pipeline/download/{__init__,sabs,tiger,acs,ccd}.py` each exposing `fetch()`. `pipeline/download.py` orchestrator: idempotent (skips existing files unless `--force`), prints status table. Test fixtures: `tests/fixtures/acs_b19131_sample.json` (3 BGs), `tests/fixtures/ccd_sample.csv` (5 rows).

**Tests** (all network-mocked with `responses`):
- `test_acs.py`: `test_fetch_table_builds_correct_url` (asserts URL contains `B19131`, `state:53`, `block group:*`), `test_fetch_table_parses_response`, `test_missing_api_key_raises`.
- `test_sabs.py::test_filter_to_wa` (mixed STATEFP fixture → only `53` survives).
- `test_ccd.py::test_filter_to_wa_and_required_columns`.
- `test_orchestrator.py`: `test_skips_existing_files_without_force`, `test_force_redownloads`.

**Exit:** all unit tests pass with no network access (CI-safe). Manual `python pipeline/download.py` with real `CENSUS_API_KEY` populates `data/raw/`. Re-run is a no-op.

**Depends on:** Phase 2.

---

## Phase 5 — Pipeline core: spatial join + areal interpolation

**Goal:** Geometric heart of the pipeline as small, pure, individually testable functions on **synthetic geometries**. No real data required.

**Deliverables:** [pipeline/build_dataset.py](pipeline/build_dataset.py) split into:
- `prepare_layers(sabs, bgs, blocks)` — reproject to `TARGET_CRS`, validate columns, `make_valid` invalid geometries.
- `attach_acs(bgs, b19131, b11005, b19013)` — join ACS tables onto BG GEOIDs.
- `interpolate_to_zones(zones, bgs_with_acs, blocks, extensive_cols)` — wraps `tobler.area_weighted.area_interpolate` with blocks as the population auxiliary layer.
- `compute_summary_stats(zone_row)` — median (linear interpolation across bracket cumulatives), shares, total, histogram.
- `flag_low_confidence(record) -> (bool, list[str])` — flags `missing_sabs` (null geometry) and `low_household_count` (<50).

[pipeline/brackets.py](pipeline/brackets.py) — frozen mapping of B19131 column codes → `(lower, upper, label)`. Single source of truth for the histogram schema.

**Tests** (synthetic geometries throughout):
- `test_brackets.py::test_brackets_cover_full_range` (contiguous, non-overlapping, top open).
- `test_interpolate.py`:
  - `test_full_zone_contains_one_bg` — zone fully containing a BG receives 100% of counts.
  - `test_half_zone_gets_half` — half-coverage → ~50% within 1%.
  - **`test_population_weighting_skews_correctly`** — two equal-area BG halves with population concentrated in one; zone covering the populated half receives ~100%, not 50%. **Core correctness test.**
- `test_summary_stats.py`: `test_median_interpolation` (within $500 of hand-computed), `test_share_under_35k`, `test_zero_households_returns_nulls`.
- `test_low_confidence.py`: `test_missing_sabs_flagged`, `test_low_count_flagged`, `test_both_reasons_combine`, `test_healthy_record_not_flagged`.

**Exit:** all synthetic tests pass; coverage of core functions ≥ 90%.

**Depends on:** Phase 2.

---

## Phase 6 — Pipeline orchestration & CCD join

**Goal:** Wire Phase 5 helpers into end-to-end `main()`, join CCD on `NCESSCH`, write both JSON outputs matching the Phase 3 schema.

**Deliverables:**
- `build_dataset.py::main()` — load raw inputs, call helpers in order, join CCD, apply `flag_low_confidence`, write `data/processed/schools_wa.json` + `search_index.json`.
- `join_ccd(zones, ccd)` — left join on NCESSCH; schools missing from SABS kept with null geometry, flagged low-confidence (preserves CCD-only schools rather than dropping them).
- `serialize_schools(records) -> (full, slim)` — produces both payloads conforming to `docs/schema.md`.

**Tests:**
- `test_join_ccd.py::test_inner_join_keeps_matched` — 3 zones × 4 CCD rows → 3 matched + 1 missing carried through with low-confidence.
- `test_serialize.py`: `test_payload_matches_schema` (reuses Phase 3 validators against generated payload), `test_search_index_subset` (exactly `{nces_id, name, district, city}`), `test_output_size_under_5mb` (synthetic 2,400-school payload).
- `test_main.py::test_end_to_end_synthetic` — marked `@pytest.mark.slow`; tiny synthetic raw dataset (3 schools, 5 BGs, 20 blocks) on `tmp_path`, monkeypatched `RAW_FILES`, asserts both JSON files written and pass schema validation.

**Exit:** all unit tests pass; `python pipeline/build_dataset.py` against real data produces both files in <10 min, `schools_wa.json` < 5 MB; **frontend from Phase 3 loads the real generated file unchanged** (schema lock holds).

**Depends on:** Phases 4 and 5.

---

## Phase 7 — Validation script & FRL correlation gate

**Goal:** `validate.py` computes FRL correlation, coverage report, machine-readable pass/fail. **FRL gate (r ≥ 0.6) blocks shipping** — spec explicitly says near-zero means the spatial join is broken.

**Deliverables:**
- [pipeline/validate.py](pipeline/validate.py):
  - `frl_correlation(schools, ccd) -> float` — Pearson r between `share_under_35k` and FRL eligibility, computed only over non-low-confidence schools.
  - `coverage_report(schools) -> dict` — counts of total, missing SABS, low household count, low-confidence total.
  - `main()` — prints both, exits `1` if `r < 0.6` or coverage thresholds fail.
- [docs/spot_checks.md](docs/spot_checks.md) — manual checklist of 5 named schools (Seattle, Bellevue, eastern WA, tribal-area, Mercer Island). Manual gate before deploy.

**Tests:**
- `test_validate.py`: `test_frl_correlation_synthetic_strong` (perfect correlation → r > 0.99), `test_frl_correlation_synthetic_weak` (uncorrelated → r near 0), `test_main_exits_nonzero_on_low_correlation` (uses `pytest.raises(SystemExit)` with `code == 1`), `test_main_exits_zero_on_healthy_data`, `test_coverage_report_counts`, `test_correlation_excludes_low_confidence` (low-confidence outlier doesn't pull r down).

**Exit:** unit tests pass; `python pipeline/validate.py` against real data prints r > 0.6, exits 0; `docs/spot_checks.md` filled with real observed values, all 5 plausible.

**Depends on:** Phase 6.

---

## Phase 8 — Frontend wiring against real data + low-confidence UX

**Goal:** Replace fake JSON with the real generated file; verify graceful degradation on low-confidence schools; polish disclaimer copy.

**Deliverables:**
- [site/app.js](site/app.js) updates: explicit warning text for `low_confidence=true`, listing human-readable reasons (e.g., "This school's attendance boundary was not available; estimates may be unreliable").
- [site/index.html](site/index.html): disclaimer reviewed for accuracy, link to methodology.
- [docs/methodology.md](docs/methodology.md) — 1-page non-technical explainer of dasymetric interpolation, "zoned" semantics, B19131, known SABS gaps in WA.
- `tests/site/test_real_data.py` — Phase 3 schema validators re-run against the real generated `data/processed/schools_wa.json`; `test_low_confidence_share_reasonable` (< 30%); `test_search_index_size` (1,500 < count < 3,500); `test_no_orphan_search_index_entries`.

**Exit:** local server serves working app against real data; "Mercer Island" / "Garfield" / "Bellevue" return plausible histograms; a low-confidence school visibly carries the warning.

**Depends on:** Phases 6 and 7 (gate must pass).

---

## Phase 9 — Reproducibility, CI & deploy

**Goal:** Lock the build into CI; deploy to GitHub Pages.

**Deliverables:**
- `.github/workflows/test.yml` — `pytest -m "not slow and not integration"` on Python 3.11 every push.
- `.github/workflows/pages.yml` — deploys `site/` + committed `data/processed/` to GitHub Pages on push to `main`.
- `Makefile` (or `tasks.py` for cross-platform): `install`, `test`, `download`, `build`, `validate`, `serve`.
- `README.md` — quickstart, ASCII architecture diagram, known-gaps section linking to validate output.
- Optional: `tests/test_repo_conventions.py::test_data_raw_gitignored` (reads `.gitignore`, asserts `data/raw/` listed).

**Exit:** clean clone + `make install && make test` passes; CI green on `main`; GitHub Pages URL serves the working app.

**Depends on:** Phase 8.

---

## Phase 10 — Post-MVP readiness checklist

**Goal:** Document hooks for the spec's 8 extension paths so future work is additive, not a rewrite.

**Deliverables:** [docs/post_mvp.md](docs/post_mvp.md) with one short section per extension path describing where the change lands (e.g., "More states: parameterize `STATE_FIPS` in `pipeline/io.py` to a list; switch `data/processed/` to per-school files when payload exceeds 10 MB"). `# TODO(post-mvp):` comment in `pipeline/io.py` next to `STATE_FIPS`.

**Tests:** none.

**Exit:** PR review.

**Depends on:** Phase 9.

---

## Critical files

- [pipeline/io.py](pipeline/io.py) — single source of truth for paths, CRS, FIPS, ACS vintage.
- [pipeline/build_dataset.py](pipeline/build_dataset.py) — geometric core; must stay decomposed into pure helpers.
- [pipeline/brackets.py](pipeline/brackets.py) — frozen B19131 bracket mapping.
- [pipeline/validate.py](pipeline/validate.py) — FRL gate; nonzero exit blocks deploy.
- [docs/schema.md](docs/schema.md) — JSON contract, locked in Phase 3, re-validated in Phases 6 & 8.
- [site/app.js](site/app.js) — only place low-confidence reasons get rendered.

## End-to-end verification

1. `pip install -e ".[dev]"` then `pytest` — full unit suite green (excludes `slow`/`integration`).
2. `pytest -m slow` — runs the synthetic end-to-end pipeline test in Phase 6.
3. `python pipeline/download.py` (with `CENSUS_API_KEY` set) — populates `data/raw/`.
4. `python pipeline/build_dataset.py` — produces `data/processed/{schools_wa,search_index}.json` in < 10 min, < 5 MB.
5. `python pipeline/validate.py` — exits 0 with FRL r ≥ 0.6.
6. Manually fill in `docs/spot_checks.md` against the live UI for the 5 named schools.
7. `python -m http.server 8000 --directory site` — search/select flows work; low-confidence schools show the warning.
8. Push to `main` — CI green, Pages deploys.

## Notable design choices (deviations from spec, flag-worthy)

- **`download/` package, not single `download.py`** — five sources have different protocols and failure modes; per-source split makes mocking and idempotency cleaner. Spec said "single script" but the orchestrator pattern preserves the user-facing CLI.
- **EPSG:2927 (WA State Plane South), not 3857** — spec offered both; chose the projected/equal-area option because tobler's areal interpolation is sensitive to area distortion.
- **Frontend stub before pipeline (Phase 3 parallel)** — locks the JSON schema early via a hand-authored fixture; Phase 6's serializer must match it. Eliminates schema-coordination churn.
- **FRL r ≥ 0.6 is a hard CI gate** — spec describes it as a sanity check; treating it as blocking because shipping a broken dataset poisons the site's purpose.
