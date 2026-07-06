#!/usr/bin/env python3
"""Mitogenome upload status report.

Usage:
    singularity run $SING/psycopg2:0.1.sif python mitogenome_upload_status.py \
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
   Mitogenome upload status

   Scope: one row per og_id (excluding "_concat" merged records),
   using the best available sequencing technology (priority
   hifi > hic > ilmn) and its latest assembly attempt (seq_date and code), matched
   to a sample record for embargo_status.

   Upload status logic:
   - '<tech> uploaded'   genbank_accession present for the
                         chosen technology, e.g. 'hifi uploaded'.
   - 'needs_uploading'   no genbank_accession yet.
----------------------------------------------------------- */
WITH base AS (
  SELECT
      m.og_num,
      m.og_id,
      m.tech,
      m.seq_date,
      m.code,
      m.genbank_accession,
      s.embargo_status,
      CASE
        WHEN m.genbank_accession IS NOT NULL
             AND BTRIM(m.genbank_accession) <> '' THEN 1
        ELSE 0
      END AS has_accession,
      CASE
        WHEN m.seq_date ~ '^[0-9]{6}$' THEN TO_DATE(m.seq_date, 'YYMMDD')
        ELSE NULL
      END AS seq_date_dt
  FROM mitogenome_data m
  LEFT JOIN sample s ON s.og_id = m.og_id
  WHERE m.og_id NOT LIKE '%_concat%'
),
dedup AS (
  SELECT *
  FROM (
    SELECT
      b.*,
      ROW_NUMBER() OVER (
        PARTITION BY b.og_id, LOWER(b.tech)
        ORDER BY b.seq_date_dt DESC NULLS LAST, b.seq_date DESC, b.code DESC
      ) AS rn
    FROM base b
  ) x
  WHERE x.rn = 1
),
og_flags AS (
  SELECT
    og_id,
    MAX(CASE WHEN LOWER(tech) = 'hifi' THEN 1 ELSE 0 END) AS has_hifi,
    MAX(CASE WHEN LOWER(tech) = 'hic'  THEN 1 ELSE 0 END) AS has_hic,
    MAX(CASE WHEN LOWER(tech) = 'ilmn' THEN 1 ELSE 0 END) AS has_ilmn
  FROM dedup
  GROUP BY og_id
),
chosen AS (
  SELECT
    d.*,
    CASE
      WHEN f.has_hifi = 1 THEN 'hifi'
      WHEN f.has_hic  = 1 THEN 'hic'
      WHEN f.has_ilmn = 1 THEN 'ilmn'
      ELSE NULL
    END AS chosen_tech
  FROM dedup d
  JOIN og_flags f USING (og_id)
)
SELECT
  og_num,
  og_id,
  tech,
  seq_date,
  code,
  genbank_accession,
  embargo_status,
  CASE
    WHEN has_accession = 1 THEN LOWER(chosen_tech) || ' uploaded'
    ELSE 'needs_uploading'
  END AS upload_status
FROM chosen
WHERE LOWER(tech) = LOWER(chosen_tech)
ORDER BY og_id
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
