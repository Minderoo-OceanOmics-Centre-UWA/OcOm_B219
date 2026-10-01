#!/usr/bin/env python3
"""Mitogenome upload status report.

Reports ENA submission status for mitogenomes, one row per OG using the
best available sequencing technology (priority: hifi > hic > ilmn).
Filters: excludes "_concat" OG IDs, deduplicates to the most recent record
per OG + technology, selects one technology per OG by priority.

upload_status (based on ena_validation_latest/ena_submissions, not the
dead mitogenome_data.genbank_accession column):
    submitted, public                ena_submissions.ena_assembly_accession
                                      holds the public GCA_ accession.
    submitted, accession assigned    ena_submissions has an ACCESSION_ASSIGNED
                                      row (production mode) for this exact
                                      assembly+annotation, but no GCA yet.
    submission failed                ena_submissions row is FAILED.
    not submitted                    ena_submissions row is NOT_SUBMITTED (no
                                      receipt.xml was found on disk).
    ready to submit                  no submission yet, but the flatfile has
                                      cleared every format-validation gate
                                      the Mitogenomes pipeline runs
                                      (ena_validation_latest.submission_ready).
    needs_uploading                  catch-all: not yet submission_ready.

data_quality_flag (informational, independent of upload_status; matches
lca_validation against this record's seq_date and its chosen technology;
also matches ENA-mito-genomes/00_create_mito_samplesheet.py's own not-ready
reasons where applicable):
    no validated species name recorded      no lca_validation row at all has one.
    no <tech> validation matching seq_date  a validated name exists, but not
                                             from a row matching the OG's chosen
                                             technology AND seq_date — fell back
                                             to the latest overall.
    species mismatch: validated vs nominal  disagrees with sample.nominal_species_id.
    missing ncbi_id                         sample.ncbi_id is blank — no
                                             BioSample can be created without
                                             it. Checked independently of
                                             TOLID_SQL, since mitogenomes
                                             assembled via hic/ilmn (not hifi)
                                             never appear in TOLID_SQL's
                                             sample.hifi='Y' population.

Full SQL + comments: queries.py (MITO_SQL).

Usage:
    singularity run $SING/psycopg2:0.1.sif python mitogenome_upload_status.py \
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
from queries import MITO_SQL as sql

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
out_path = Path(__file__).resolve().parent.parent / "reports" / f"mito_status_{stamp}.csv"
out_path.parent.mkdir(parents=True, exist_ok=True)

with open(out_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    writer.writerows(rows)
print(f"Written to {out_path}")

print(f"\nTotal: {len(rows)} OGs")
for status, count in sorted(Counter(dict(zip(cols, r))['upload_status'] for r in rows).items()):
    print(f"  {status}: {count}")

flag_idx = cols.index('data_quality_flag')
flagged_rows = [r for r in rows if r[flag_idx]]

flag_path = out_path.with_name(f"{out_path.stem}_data_quality_issues{out_path.suffix}")
with open(flag_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    writer.writerows(flagged_rows)
print(f"\nData quality issues ({len(flagged_rows)} rows) written to {flag_path}")

conn.close()
