#!/usr/bin/env python3
"""Build the OG Upload Status HTML report from combined_upload_status.py's CSV output.

Reads the pivoted combined CSV (og_id, species, embargo_status,
draft/ref/mito upload_status + data_quality_flag), embeds it as JSON into
report_template.html, and writes a self-contained HTML file. This only
regenerates the local HTML file — it does NOT publish/update the Claude
artifact, since that step requires Claude itself (a plain script can't do
it). Re-run this after combined_upload_status.py, then ask Claude to
republish the output file if you want the live artifact link updated too.

Usage:
    python3 generate_html_report.py [og_status.csv] [output.html]

    Both arguments are optional:
      - defaults to og_status.csv / og_status_report.html
        in ../reports/ (a sibling of this script's directory), not here.
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS_DIR = HERE.parent / "reports"
TEMPLATE_PATH = HERE / "report_template.html"
PLACEHOLDER = "__DATA_JSON__"


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPORTS_DIR / "og_status.csv"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPORTS_DIR / "og_status_report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        sys.exit(f"Error: {csv_path} not found. Run combined_upload_status.py first.")
    if not TEMPLATE_PATH.exists():
        sys.exit(f"Error: template not found at {TEMPLATE_PATH}.")

    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))

    template = TEMPLATE_PATH.read_text()
    if PLACEHOLDER not in template:
        sys.exit(f"Error: placeholder {PLACEHOLDER} not found in template — has it been edited?")

    html = template.replace(PLACEHOLDER, json.dumps(rows))
    out_path.write_text(html)

    print(f"Read {len(rows)} rows from {csv_path}")
    print(f"Written {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")


if __name__ == '__main__':
    main()
