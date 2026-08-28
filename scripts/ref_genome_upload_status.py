#!/usr/bin/env python3
"""Reference genome upload status report.

Reports NCBI upload status for reference genomes at assembly stage 3.
Filters: OGs with a validated species name in lca_validation (deduplicated
to the most recent record per OG) and a stage 3 entry in ref_genomes.

upload_status is either a fixed value, or a composite of up to three parts
(hap1 status, hap2 status, raw data status) joined with ", ":
    not registered on NCBI            no entry in ref_genomes_assembly_uploads,
                                       and none of sample's NCBI ID columns hold
                                       a value.
    registered in sample, pending sync   no entry yet, but a sample column does
                                       (registration started upstream, hasn't
                                       synced down).
    all uploaded                      both haplotype assembly accessions (JBXXXX)
                                       present AND at least one SRR released.
    hap1/hap2 uploaded / pending      assembly accession assigned / BioProject
                                       registered but no assembly accession yet.
    raw data <status>                 all known SRR statuses for the OG agree.
    raw <type> <status>, ...          SRR statuses differ across data types,
                                       listed separately.
    raw data pending                  rawdata BioProject registered, no SRR yet.

data_quality_flag (informational, independent of upload_status; matches
lca_validation against the stage-3 seq_date, preferring hifi over hic; also
matches ENA-ref-genomes/00_pull_ready_to_upload_samples.py's own checks —
note that script treats several of these as HARD exclusions rather than
just flags, so an OG flagged here may be rejected there even though it
still shows up in this report):
    no hifi/hic validation matching stage-3 seq_date   fell back to the latest
                                                        validated record overall.
    species mismatch: validated vs nominal             disagrees with
                                                        sample.nominal_species_id.
    validated via hic only (ENA pipeline requires hifi)   the matched validation
                                                        came from a hic row, not
                                                        hifi — that pipeline only
                                                        ever accepts tech='hifi'.
    hap1/hap2 stage-3 seq_date inconsistent             the OG's stage-3
                                                        ref_genomes rows don't
                                                        all share the same
                                                        seq_date.

Full SQL + comments: queries.py (REF_SQL).

Usage:
    singularity run $SING/psycopg2:0.1.sif python ref_genome_upload_status.py \
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
from queries import REF_SQL as sql

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
out_path = Path(__file__).resolve().parent.parent / "reports" / f"ref_status_{stamp}.csv"
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
