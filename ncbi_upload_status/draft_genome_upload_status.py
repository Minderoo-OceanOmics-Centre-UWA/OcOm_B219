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
WITH eligible_ogs AS (
  SELECT s.og_id
  FROM sample s
  WHERE (
          s.workflow = 'Draft'
          OR (
               s.workflow IN ('Not assigned', 'N/A', '')
               AND s.il_status = 'Sequenced'
             )
        )
    AND s.ncbi_bioproject_id_draft IS NULL
    AND s.draft_sra_accessions IS NULL
    AND s.draft_assembly_accession IS NULL

  UNION

  SELECT dg.og_id
  FROM draft_genomes dg
  JOIN sample s ON s.og_id = dg.og_id
  WHERE (dg.biosample_accession IS NULL OR dg.biosample_accession !~* '^SAM')
    AND (dg.bioproject_accession IS NULL OR dg.bioproject_accession !~* '^PRJ')
    AND (dg.sra_accession IS NULL OR dg.sra_accession !~* '^SRR')
)
SELECT
    s.og_id,
    s.ncbi_bioproject_id_lvl_3_hifi,
    s.ncbi_biosample_id,
    s.ncbi_bioproject_id_draft,
    dg.biosample_accession,
    dg.bioproject_accession,
    s.draft_sra_accessions,
    dg.sra_accession,
    s.nominal_species_id,
    dg.aws_assm,
    s.workflow,
    s.il_status,
    s.embargo_status,
    CASE
      WHEN dg.aws_assm IS NULL THEN 'missing .fna'
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.bioproject_accession IS NOT NULL AND dg.bioproject_accession ~* '^PRJ')
       AND (
             (dg.sra_accession IS NOT NULL AND dg.sra_accession ~* '^SRR')
             OR
             (s.draft_sra_accessions IS NOT NULL AND s.draft_sra_accessions ~* 'SRR[0-9]+')
           )
      THEN 'uploaded'
      ELSE 'needs_uploading'
    END AS upload_status
FROM sample s
JOIN eligible_ogs e ON s.og_id = e.og_id
LEFT JOIN draft_genomes dg ON dg.og_id = s.og_id
ORDER BY s.og_id
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
