#!/bin/bash
# Usage: bash run_combined_report.sh <config> [--check] [output.csv]

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
LISTING="$REPORTS_DIR/public_release_listing.txt"
# fpdf2 + pypdf are not in the Pawsey python module; override to use another interpreter.
PDF_PYTHON="${PDF_PYTHON:-/software/projects/pawsey1348/olivianguyen/tools/ocom_pdf/bin/python}"

if [ "$CHECK_ONLY" = false ]; then
    echo "=== Refreshing the open-data bucket listing -> $LISTING ==="
    python3 "$SRC_DIR/public_release.py" "$LISTING"
    echo
fi

# Both of these run in either mode: the report is only as current as the accessions and
# taxon ids it reads, and both writes are blank-only, so there is nothing to hold back
# during the quarter.
echo "=== Filling GCA accessions from ENA (writes to the DB) ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/resolve_gca_accessions.py" "$CONFIG"

echo
echo "=== Backfilling NCBI taxon IDs for fully validated OGs (writes to the DB) ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/backfill_ncbi_taxon_id.py" "$CONFIG"

echo
echo "=== Querying DB -> $OUT_CSV ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/combined_upload_status.py" "$CONFIG" "$OUT_CSV" --listing "$LISTING"

echo
echo "=== Building action items list -> $ACTION_ITEMS_TXT ==="
singularity run "$SING/psycopg2:0.1.sif" python3 "$SRC_DIR/action_items_report.py" "$CONFIG" "$ACTION_ITEMS_TXT"

if [ "$CHECK_ONLY" = true ]; then
    echo
    echo "Check-only run: GCA accessions and taxon IDs were backfilled, but no PDF was built"
    echo "or archived, and the open-data listing was reused as-is."
    echo "Action items at $ACTION_ITEMS_TXT. Run without --check for the quarterly PDF."
else
    echo
    echo "=== Building summary report -> $SUMMARY_PDF ==="
    "$PDF_PYTHON" "$SRC_DIR/generate_summary_report.py" "$OUT_CSV" "$SUMMARY_PDF"

    STAMP="$(date +%Y%m%d_%H%M%S)"
    cp "$SUMMARY_PDF" "$ARCHIVE_DIR/$(basename "$SUMMARY_PDF" .pdf)_$STAMP.pdf"

    echo
    echo "Archived a dated copy of the summary PDF to $ARCHIVE_DIR/ (suffix _$STAMP)"
    echo "Done. $SUMMARY_PDF is the manager-facing summary. Action items at $ACTION_ITEMS_TXT."
fi
