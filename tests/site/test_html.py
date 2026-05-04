"""Lightweight HTML-structure regression checks for the static site."""
from pipeline import io

INDEX_HTML = io.REPO_ROOT / "site" / "index.html"


def test_index_has_count_percent_toggle():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="toggle-count"' in html
    assert 'id="toggle-percent"' in html


def test_index_has_persistent_disclaimer():
    """The zoned-vs-enrolled language must remain present — it's the
    spec's load-bearing UX disclaimer."""
    html = INDEX_HTML.read_text(encoding="utf-8").lower()
    assert "zoned" in html
    assert "enrolled" in html


def test_index_loads_chart_and_search_libraries():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "chart.js" in html.lower() or "chart.umd" in html.lower()
    assert "minisearch" in html.lower()
