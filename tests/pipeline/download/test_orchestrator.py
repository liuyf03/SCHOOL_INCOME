"""Tests for the pipeline.download orchestrator."""
from pathlib import Path

import pytest

from pipeline import download
from pipeline.download import acs, ccd, sabs, tiger


def _stub_fetch(label_prefix, force_log, status="downloaded"):
    """Build a fake fetch() that records the force argument and returns a result."""
    def _fake(force=False):
        force_log.append((label_prefix, force))
        return [(label_prefix, status, Path(f"/fake/{label_prefix.lower()}"))]
    return _fake


def _patch_all_fetchers(monkeypatch, force_log, status="downloaded"):
    monkeypatch.setattr(sabs, "fetch", _stub_fetch("SABS", force_log, status))
    monkeypatch.setattr(tiger, "fetch", _stub_fetch("TIGER", force_log, status))
    monkeypatch.setattr(acs, "fetch", _stub_fetch("ACS", force_log, status))
    monkeypatch.setattr(ccd, "fetch", _stub_fetch("CCD", force_log, status))


def test_skips_existing_files_without_force(tmp_path, monkeypatch):
    """When all RAW_FILES targets exist, every fetcher returns 'skipped'
    without making a network call. We pre-create files and rely on the
    real fetch() implementations' idempotency check."""
    fake_paths = {key: tmp_path / f"{key}.fake" for key in download.acs.RAW_FILES}
    for path in fake_paths.values():
        path.write_bytes(b"already here")
    # Patch RAW_FILES on every fetcher module — they each grabbed a reference
    # at import time, so monkeypatching pipeline.io alone is not enough.
    for module in (sabs, tiger, acs, ccd):
        monkeypatch.setattr(module, "RAW_FILES", fake_paths)

    results = download.fetch_all(force=False)

    assert len(results) > 0
    for label, status, _ in results:
        assert status == "skipped", f"{label} was not skipped"


def test_fetch_all_passes_force_to_each_module(monkeypatch):
    force_log = []
    _patch_all_fetchers(monkeypatch, force_log)

    download.fetch_all(force=True)

    assert len(force_log) == 4
    assert all(force is True for _, force in force_log)


def test_fetch_all_collects_results_from_each_module(monkeypatch):
    force_log = []
    _patch_all_fetchers(monkeypatch, force_log)

    results = download.fetch_all(force=False)

    labels = {label for label, _, _ in results}
    assert labels == {"SABS", "TIGER", "ACS", "CCD"}


def test_fetch_all_records_module_failures(monkeypatch):
    def boom(force=False):
        raise RuntimeError("network down")

    monkeypatch.setattr(sabs, "fetch", boom)
    force_log = []
    monkeypatch.setattr(tiger, "fetch", _stub_fetch("TIGER", force_log))
    monkeypatch.setattr(acs, "fetch", _stub_fetch("ACS", force_log))
    monkeypatch.setattr(ccd, "fetch", _stub_fetch("CCD", force_log))

    results = download.fetch_all(force=False)

    statuses = {label: status for label, status, _ in results}
    assert "error" in statuses["pipeline.download.sabs"]
    assert statuses["TIGER"] == "downloaded"


def test_main_returns_zero_on_success(monkeypatch, capsys):
    force_log = []
    _patch_all_fetchers(monkeypatch, force_log, status="downloaded")

    rc = download.main(force=False)

    assert rc == 0
    captured = capsys.readouterr()
    assert "SABS" in captured.out
    assert "downloaded" in captured.out


def test_main_returns_nonzero_when_a_module_errors(monkeypatch):
    def boom(force=False):
        raise RuntimeError("nope")

    monkeypatch.setattr(sabs, "fetch", boom)
    force_log = []
    monkeypatch.setattr(tiger, "fetch", _stub_fetch("TIGER", force_log))
    monkeypatch.setattr(acs, "fetch", _stub_fetch("ACS", force_log))
    monkeypatch.setattr(ccd, "fetch", _stub_fetch("CCD", force_log))

    rc = download.main(force=False)

    assert rc == 1


def test_print_status_table_handles_empty(capsys):
    download.print_status_table([])
    assert "no fetchers" in capsys.readouterr().out
