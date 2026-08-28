#!/bin/bash
# Run the combined upload status report end-to-end: query the DB
# (scripts/combined_upload_status.py), then build the action-items to-do
# list (scripts/action_items_report.py) — one command instead of two.
#
# Output (CSV + data quality issues CSV + action-items CSV + summary PDF) is
# written to reports/, outside the scripts/ directory. A dated copy of the
# summary PDF is also saved to reports/archive/ on every run, so past
# quarters aren't overwritten and can be compared against later — the PDF
# embeds its own counts, so that's the only file that needs to be archived.
#
# Pass --check to skip the PDF/archive step and only refresh the CSVs — use
# this for as-needed triage during the quarter, since it never touches
# reports/archive/. Omit it for the official quarterly run whose PDF gets
# archived and sent to management.
#
# Usage:
#   bash run_combined_report.sh <config> [--check] [output.csv]
#
# Examples:
#   bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg --check
#   bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$ROOT_DIR/scripts"
REPORTS_DIR="$ROOT_DIR/reports"
ARCHIVE_DIR="$REPORTS_DIR/archive"
mkdir -p "$REPORTS_DIR" "$ARCHIVE_DIR"

CHECK_ONLY=false
ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--check" ]; then
        CHECK_ONLY=true
    else
        ARGS+=("$arg")
    fi
done

CONFIG="${ARGS[0]:?Usage: run_combined_report.sh <config> [--check] [output.csv]}"
OUT_CSV="${ARGS[1]:-$REPORTS_DIR/og_status.csv}"
ACTION_ITEMS_TXT="$(dirname "$OUT_CSV")/$(basename "$OUT_CSV" .csv)_action_items.txt"
SUMMARY_PDF="$(dirname "$OUT_CSV")/$(basename "$OUT_CSV" .csv)_summary.pdf"

echo "=== Step 1/2: querying DB -> $OUT_CSV ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/combined_upload_status.py" "$CONFIG" "$OUT_CSV"

echo
echo "=== Step 2/2: building action items list -> $ACTION_ITEMS_TXT ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/action_items_report.py" "$CONFIG" "$ACTION_ITEMS_TXT"

if [ "$CHECK_ONLY" = true ]; then
    echo
    echo "Check-only run: no PDF generated, nothing archived."
    echo "Open $ACTION_ITEMS_TXT to see what needs action this quarter."
    echo "Run without --check to also build the manager PDF and archive it."
else
    echo
    echo "=== Step 3/3: building summary report -> $SUMMARY_PDF ==="
    # Run before archiving this PDF below, so it finds and compares against the
    # previously archived PDF rather than itself.
    python3 "$SRC_DIR/generate_summary_report.py" "$OUT_CSV" "$SUMMARY_PDF"

    STAMP="$(date +%Y%m%d_%H%M%S)"
    cp "$SUMMARY_PDF" "$ARCHIVE_DIR/$(basename "$SUMMARY_PDF" .pdf)_$STAMP.pdf"

    echo
    echo "Archived a dated copy of the summary PDF to $ARCHIVE_DIR/ (suffix _$STAMP)"
    echo "Done. $SUMMARY_PDF is the manager-facing summary. Action items at $ACTION_ITEMS_TXT."
fi
