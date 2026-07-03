#!/usr/bin/env python3
"""Draft genome upload status report.

Usage:
    singularity run $SING/psycopg2:0.1.sif python draft_genome_upload_status.py \
        ~/postgresql_details/oceanomics.cfg [output.csv]
"""
import configparser, psycopg2, csv, sys
from collections import Counter
from pathlib import Path

cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
p = cfg["postgres"]
conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                        host=p["host"], port=int(p["port"]))
cur = conn.cursor()

sql = """
/* -----------------------------------------------------------
   Draft genome upload status

   Scope: one row per og_id (the latest seq_date, for og_ids
   with multiple draft_genomes entries), matched to a sample
   record.

   Upload status logic (checked top-down, first match wins; the
   first three all require aws_assm to be present):
   - 'all uploaded'                  biosample_accession, bioproject_accession,
                                      sra_accession, AND assembly_accession all
                                      present and validly formatted.
   - 'raw uploaded, assembly
      pending'                       biosample_accession, bioproject_accession,
                                      AND sra_accession present, but
                                      assembly_accession still NULL.
   - 'raw+assembly needs
      uploading'                     assembly file ready (aws_assm present)
                                      but biosample_accession, bioproject_accession,
                                      sra_accession, AND assembly_accession are
                                      all NULL - nothing registered yet.
   - 'missing .fna or fields'        catch-all: aws_assm is NULL (no assembly
                                      file yet), or any other partial state not
                                      covered above.
----------------------------------------------------------- */
WITH latest AS (
    SELECT DISTINCT ON (og_id) *
    FROM draft_genomes
    ORDER BY og_id, seq_date DESC
)
SELECT
    dg.og_id,
    s.nominal_species_id,
    dg.aws_assm,
    dg.biosample_accession,
    dg.bioproject_accession,
    dg.sra_accession,
    dg.assembly_accession,
    s.embargo_status,
    CASE
      WHEN dg.aws_assm IS NOT NULL
       AND (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.bioproject_accession IS NOT NULL AND dg.bioproject_accession ~* '^PRJ')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^SRR')
       AND NULLIF(dg.assembly_accession, '') IS NOT NULL
        THEN 'all uploaded'
      WHEN dg.aws_assm IS NOT NULL
       AND (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.bioproject_accession IS NOT NULL AND dg.bioproject_accession ~* '^PRJ')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^SRR')
       AND NULLIF(dg.assembly_accession, '') IS NULL
        THEN 'raw uploaded, assembly pending'
      WHEN dg.aws_assm IS NOT NULL
       AND dg.biosample_accession IS NULL
       AND dg.bioproject_accession IS NULL
       AND dg.sra_accession IS NULL
       AND NULLIF(dg.assembly_accession, '') IS NULL
        THEN 'raw+assembly needs uploading'
      ELSE 'missing .fna or fields'
    END AS upload_status
FROM latest dg
JOIN sample s ON s.og_id = dg.og_id
ORDER BY dg.og_id
"""

cur.execute(sql)
rows = cur.fetchall()
cols = [d[0] for d in cur.description]

out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
if out_path:
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)
    print(f"Written to {out_path}")
else:
    print('\t'.join(cols))
    for row in rows:
        print('\t'.join('' if x is None else str(x) for x in row))

print(f"\nTotal: {len(rows)} OGs")
for status, count in sorted(Counter(dict(zip(cols, r))['upload_status'] for r in rows).items()):
    print(f"  {status}: {count}")

conn.close()
