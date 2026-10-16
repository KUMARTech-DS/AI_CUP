#!/usr/bin/env python3
"""Install the updated Ads Lab HTML into the SuffAI command-center repo.

Put this file and ads_lab_updated.html in the repository root,
next to the web/ directory, then run:
    python3 install_ads_lab_updated.py
The script backs up web/ads_lab.html before replacing it.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root (default: current directory)")
    parser.add_argument("--source", type=Path, default=None, help="Replacement HTML path (default: <repo>/ads_lab_updated.html)")
    parser.add_argument("--check-only", action="store_true", help="Validate source without installing")
    args = parser.parse_args()

    repo = args.repo.resolve()
    source = (args.source or (repo / "ads_lab_updated.html")).resolve()
    target = repo / "web" / "ads_lab.html"

    if not (repo / "command_center").is_dir() or not (repo / "web").is_dir():
        print(f"ERROR: {repo} does not look like the AI_CUP repo root (expected web/ and command_center/).", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"ERROR: replacement HTML not found: {source}", file=sys.stderr)
        print("Put the downloaded ads_lab_updated.html in the repository root or pass --source /path/to/ads_lab_updated.html.", file=sys.stderr)
        return 2

    html = source.read_text(encoding="utf-8")
    required = [
        'id="filterArea"', 'id="filterAge"', 'id="filterGender"', 'id="filterRfm"',
        'id="creativeGrid"', 'id="audienceCoverage"', 'id="tasteCoverage"',
        "function renderTasteCoverage", "function matchScore", "function renderCreatives", "/v1/ads-lab/extract"
    ]
    missing = [marker for marker in required if marker not in html]
    if missing:
        print("ERROR: source HTML does not look like the updated Ads Lab page. Missing: " + ", ".join(missing), file=sys.stderr)
        return 2
    if "Math.random" in html or "dummyScore" in html or "items[index % items.length]" in html:
        print("ERROR: source still contains the old random-score or repeating-card logic.", file=sys.stderr)
        return 2

    print(f"Source: {source}")
    print(f"Target: {target}")
    print("Validation: OK (segment selectors, deterministic score logic, no repeating-card/random-score code)")
    if args.check_only:
        print("Check-only requested; no files changed.")
        return 0

    if not target.is_file():
        print(f"ERROR: target HTML not found: {target}", file=sys.stderr)
        return 2
    backup = target.with_name(f"ads_lab.html.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(target, backup)
    shutil.copy2(source, target)
    print(f"Backup: {backup}")
    print("Installed updated Ads Lab page. Review with: git diff -- web/ads_lab.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
