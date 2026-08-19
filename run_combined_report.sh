#!/bin/bash
# Run the combined upload status report end-to-end: query the DB
# (scripts/combined_upload_status.py), then build the standalone HTML report
# from the result (scripts/generate_html_report.py) — one command instead of two.
#
# Output (CSV + data quality issues CSV + HTML) is written to reports/,
# outside the scripts/ directory. A dated copy of both CSVs is also saved to
# reports/archive/ on every run, so past quarters aren't overwritten and can
# be compared against later (each row also carries a generated_at timestamp,
# so a saved CSV is self-describing even if renamed).
#
# Usage:
#   bash run_combined_report.sh <config> [output.csv]
#
# Example:
#   bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$ROOT_DIR/scripts"
REPORTS_DIR="$ROOT_DIR/reports"
ARCHIVE_DIR="$REPORTS_DIR/archive"
mkdir -p "$REPORTS_DIR" "$ARCHIVE_DIR"

CONFIG="${1:?Usage: run_combined_report.sh <config> [output.csv]}"
OUT_CSV="${2:-$REPORTS_DIR/og_status.csv}"
OUT_HTML="${OUT_CSV%.csv}_report.html"
FLAG_CSV="$(dirname "$OUT_CSV")/$(basename "$OUT_CSV" .csv)_flagged.csv"

echo "=== Step 1/2: querying DB -> $OUT_CSV ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/combined_upload_status.py" "$CONFIG" "$OUT_CSV"

echo
echo "=== Step 2/2: building HTML report -> $OUT_HTML ==="
python3 "$SRC_DIR/generate_html_report.py" "$OUT_CSV" "$OUT_HTML"

STAMP="$(date +%Y%m%d_%H%M%S)"
cp "$OUT_CSV" "$ARCHIVE_DIR/$(basename "$OUT_CSV" .csv)_$STAMP.csv"
cp "$FLAG_CSV" "$ARCHIVE_DIR/$(basename "$FLAG_CSV" .csv)_$STAMP.csv"

echo
echo "Archived dated copies to $ARCHIVE_DIR/ (suffix _$STAMP)"
echo "Done. Open the HTML file locally, or ask Claude to republish it if you want the shareable link updated."
