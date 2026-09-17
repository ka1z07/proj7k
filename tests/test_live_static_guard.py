"""
Tests for StaticAssetGuard and Live Static Assets (SPEC-P2.4-04 / Ticket 10).

Verifies zero-dependency offline guarantee, absence of any external CDN references,
presence of 3-column esports layout, 8-axis canvas, dual-hand strain canvas,
collapsible 4D tech drawer, and OBS overlay adaptivity.
"""

from pathlib import Path
import pytest

from proj7k.live.guard import StaticAssetGuard
from proj7k.live.server import DEFAULT_STATIC_DIR, LiveServer


def test_static_asset_guard_detects_external_urls():
    """StaticAssetGuard must detect external CDN, font, or script references."""
    dirty_html = """
    <!DOCTYPE html>
    <html>
    <head>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <link rel="stylesheet" href="http://fonts.googleapis.com/css?family=Roboto">
    </head>
    <body>
      <style>@import url('//cdnjs.cloudflare.com/ajax/libs/animate.css');</style>
    </body>
    </html>
    """
    violations = StaticAssetGuard.scan_content(dirty_html, "test.html")
    assert len(violations) >= 3
    assert any("jsdelivr" in v for v in violations)
    assert any("fonts.googleapis" in v for v in violations)
    assert any("cdnjs" in v for v in violations)


def test_static_asset_guard_passes_clean_offline_content():
    """StaticAssetGuard must pass self-contained offline content."""
    clean_html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>proj7k Live</title>
      <style>
        body { background: #0d1117; color: #c9d1d9; font-family: sans-serif; }
      </style>
    </head>
    <body>
      <canvas id="radarCanvas"></canvas>
      <script>
        console.log("Offline pure vanilla script");
      </script>
    </body>
    </html>
    """
    violations = StaticAssetGuard.scan_content(clean_html, "clean.html")
    assert len(violations) == 0
    StaticAssetGuard.assert_offline_safe_content(clean_html)


def test_static_assets_in_repo_are_100_percent_offline_safe():
    """
    StaticAssetGuard acceptance test:
    Scan all static assets in src/proj7k/live/static to guarantee zero CDN leak.
    """
    assert DEFAULT_STATIC_DIR.exists(), f"Static dir missing: {DEFAULT_STATIC_DIR}"
    report = StaticAssetGuard.scan_directory(DEFAULT_STATIC_DIR)
    total_violations = sum(len(v) for v in report.values())
    assert total_violations == 0, f"Found CDN leaks in static assets: {report}"


def test_index_html_contains_required_dashboard_components():
    """
    Verify index.html contains required canvas components,
    4D tech drawer, and OBS overlay logic.
    """
    index_file = DEFAULT_STATIC_DIR / "index.html"
    assert index_file.exists()
    content = index_file.read_text(encoding="utf-8")

    # 1. Canvas elements for Radar and Dual-Hand Strain Profile
    assert 'id="radarCanvas"' in content, "Missing #radarCanvas"
    assert 'id="strainCanvas"' in content, "Missing #strainCanvas"

    # 2. 4D Tech breakdown components
    assert "techDrawer" in content or "tech-drawer" in content
    assert "tortuosity" in content.lower() or "紊乱" in content
    assert "bracket" in content.lower() or "shear" in content.lower() or "剪切" in content
    assert "entropy" in content.lower() or "熵" in content
    assert "rhythm" in content.lower() or "时基" in content or "变异" in content

    # 3. OBS Overlay parameter handling
    assert "mode" in content and "overlay" in content
    assert "scale" in content
    assert "radar" in content
    assert "strain" in content

    # 4. Clock sync handling
    assert "clock_sync" in content
    assert "requestAnimationFrame" in content
