#!/usr/bin/env python3
"""Draft genome upload status report.

Reports NCBI upload status for draft genomes, one row per og_id (using the
latest seq_date, or the row matching an ilmn lca_validation record if one
exists, for OGs with more than one draft_genomes record). No filters —
every og_id in draft_genomes with a matching sample row is included.

upload_status (checked top-down, first match wins):
    all uploaded                     biosample_accession, bioproject_accession,
                                      sra_accession, AND assembly_accession all
                                      present and validly formatted.
    raw uploaded, assembly pending   biosample/bioproject/sra_accession present,
                                      assembly_accession still NULL.
    raw+assembly needs uploading     catch-all: nothing registered yet, or any
                                      other partial state.

data_quality_flag (informational, independent of upload_status; matches
lca_validation against this record's seq_date, tech = ilmn):
    no validated species name recorded    no lca_validation row at all has one.
    no ilmn validation matching seq_date  a validated name exists, but not from
                                           an ilmn row matching this seq_date —
                                           fell back to the latest overall.
    species mismatch: validated vs nominal   disagrees with sample.nominal_species_id.

Full SQL + comments: queries.py (DRAFT_SQL).

Usage:
    singularity run $SING/psycopg2:0.1.sif python draft_genome_upload_status.py \
        ~/postgresql_details/oceanomics.cfg

    Always writes a timestamped CSV to ../reports/ (a sibling of this
    script's directory) — no output path to specify, and nothing is ever
    overwritten.
"""
import configparser, psycopg2, csv, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import DRAFT_SQL as sql

cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
p = cfg["postgres"]
conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                        host=p["host"], port=int(p["port"]))
cur = conn.cursor()

cur.execute(sql)
rows = cur.fetchall()
cols = [d[0] for d in cur.description]

stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
out_path = Path(__file__).resolve().parent.parent / "reports" / f"draft_status_{stamp}.csv"
out_path.parent.mkdir(parents=True, exist_ok=True)

with open(out_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    writer.writerows(rows)
print(f"Written to {out_path}")

print(f"\nTotal: {len(rows)} OGs")
for status, count in sorted(Counter(dict(zip(cols, r))['upload_status'] for r in rows).items()):
    print(f"  {status}: {count}")

# --- Separate data quality issues report ---
flag_idx = cols.index('data_quality_flag')
flagged_rows = [r for r in rows if r[flag_idx]]

flag_path = out_path.with_name(f"{out_path.stem}_data_quality_issues{out_path.suffix}")
with open(flag_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    writer.writerows(flagged_rows)
print(f"\nData quality issues ({len(flagged_rows)} rows) written to {flag_path}")

conn.close()
