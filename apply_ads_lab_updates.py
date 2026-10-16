#!/usr/bin/env python3
"""Safely install the Ads Lab carousel page and segmented metrics aggregation.

Keep this installer, ads_lab_segment_carousel.html, and aggregate_metrics_updated.py
in the root of ~/AI_CUP/. Then run --check-only before installing.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()
HTML_SOURCE = ROOT / "ads_lab_segment_carousel.html"
AGG_SOURCE = ROOT / "aggregate_metrics_updated.py"
HTML_TARGET = ROOT / "web" / "ads_lab.html"
AGG_TARGET = ROOT / "command_center" / "scripts" / "aggregate_metrics.py"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate() -> tuple[str, str]:
    for path in (HTML_SOURCE, AGG_SOURCE, HTML_TARGET.parent, AGG_TARGET.parent):
        if not path.exists():
            fail(f"Required path not found: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    html = HTML_SOURCE.read_text(encoding="utf-8")
    agg = AGG_SOURCE.read_text(encoding="utf-8")
    required_html = [
        'id="filterArea"', 'id="filterAge"', 'id="filterGender"', 'id="filterRfm"',
        'id="audienceCoverage"', 'id="tasteCoverage"', 'class="creative-rail"',
        'id="creativePrev"', 'id="creativeNext"', 'garment_segment_cells'
    ]
    missing = [marker for marker in required_html if marker not in html]
    if missing:
        fail("Updated HTML is missing required markers: " + ", ".join(missing))
    if "Math.random" in html:
        fail("The updated HTML still contains Math.random scoring logic")
    try:
        ast.parse(agg, filename=str(AGG_SOURCE))
    except SyntaxError as exc:
        fail(f"Aggregation Python syntax error at line {exc.lineno}: {exc.msg}")
    for marker in ('"customer_cells": cust_list', '"audience_cells": cust_list', '"garment_segment_cells": segment_garm_list', '"pin_code", "age_group", "gender", "rfm_segment"'):
        if marker not in agg:
            fail(f"Updated aggregation file is missing expected segmented output: {marker}")
    if len(html) < 10000 or len(agg) < 5000:
        fail("One of the source files looks unexpectedly small; refusing to install")
    return html, agg


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely install Ads Lab carousel and segment aggregation files")
    parser.add_argument("--check-only", action="store_true", help="Validate inputs without changing the repository")
    args = parser.parse_args()
    html, agg = validate()
    print("Validation: OK")
    print(f"  HTML source: {HTML_SOURCE.name} ({len(html.encode('utf-8')):,} bytes)")
    print(f"  Python source: {AGG_SOURCE.name} ({len(agg.encode('utf-8')):,} bytes)")
    print("  Target: web/ads_lab.html")
    print("  Target: command_center/scripts/aggregate_metrics.py")
    if args.check_only:
        print("Check-only requested; no files changed.")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for target in (HTML_TARGET, AGG_TARGET):
        if target.exists():
            backup = target.with_name(f"{target.name}.bak-{stamp}")
            shutil.copy2(target, backup)
            print(f"Backup: {backup.relative_to(ROOT)}")

    shutil.copy2(HTML_SOURCE, HTML_TARGET)
    shutil.copy2(AGG_SOURCE, AGG_TARGET)
    # Re-validate installed content and parse Python after copy.
    installed_html = HTML_TARGET.read_text(encoding="utf-8")
    installed_agg = AGG_TARGET.read_text(encoding="utf-8")
    try:
        ast.parse(installed_agg, filename=str(AGG_TARGET))
    except SyntaxError as exc:
        fail(f"Installed aggregation Python syntax error at line {exc.lineno}: {exc.msg}")
    if 'class="creative-rail"' not in installed_html or '"garment_segment_cells": segment_garm_list' not in installed_agg:
        fail("Post-install verification failed")
    print("Installed successfully:")
    print("  web/ads_lab.html")
    print("  command_center/scripts/aggregate_metrics.py")
    print("Both previous files were backed up if they existed. Review git diff before deployment.")


if __name__ == "__main__":
    main()
