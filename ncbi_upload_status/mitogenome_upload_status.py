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

   data_quality_flag (informational, does not affect upload_status):
   - 'no validated species name
      recorded'          no lca_validation row at all has a
                         validated_species_name for this og_id.
   - 'no <tech> validation
      matching seq_date'  validated species name exists, but not from
                         a row matching the chosen technology AND
                         seq_date; fell back to the latest validated
                         record overall, regardless of tech/seq_date.
   - 'species mismatch: validated
      vs nominal'         validated_species_name disagrees with
                         sample.nominal_species_id.
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
      s.nominal_species_id,
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
),
picked AS (
  SELECT * FROM chosen WHERE LOWER(tech) = LOWER(chosen_tech)
),
matched AS (
  SELECT DISTINCT ON (lv.og_id)
      lv.og_id,
      lv.validated_species_name
  FROM lca_validation lv
  JOIN picked pk
    ON pk.og_id = lv.og_id
   AND LOWER(lv.tech) = LOWER(pk.chosen_tech)
   AND lv.seq_date = pk.seq_date
  WHERE lv.validated_species_name IS NOT NULL
    AND lv.validated_species_name <> ''
  ORDER BY lv.og_id, lv.row_created_on DESC
),
fallback AS (
  SELECT DISTINCT ON (og_id)
      og_id, validated_species_name
  FROM lca_validation
  WHERE validated_species_name IS NOT NULL
    AND validated_species_name <> ''
  ORDER BY og_id, row_created_on DESC
),
validated AS (
  SELECT
      pk.og_id,
      COALESCE(m.validated_species_name, f.validated_species_name) AS validated_species_name,
      (f.og_id IS NULL) AS no_validation,
      (f.og_id IS NOT NULL AND m.og_id IS NULL) AS used_fallback
  FROM picked pk
  LEFT JOIN matched m ON m.og_id = pk.og_id
  LEFT JOIN fallback f ON f.og_id = pk.og_id
)
SELECT
  pk.og_num,
  pk.og_id,
  pk.tech,
  pk.seq_date,
  pk.code,
  pk.genbank_accession,
  pk.embargo_status,
  v.validated_species_name,
  CONCAT_WS(', ',
      CASE WHEN v.no_validation THEN 'no validated species name recorded' END,
      CASE WHEN v.used_fallback THEN 'no ' || LOWER(pk.chosen_tech) || ' validation matching seq_date' END,
      CASE WHEN NOT v.no_validation
            AND v.validated_species_name IS DISTINCT FROM pk.nominal_species_id
           THEN 'species mismatch: validated vs nominal' END
  ) AS data_quality_flag,
  CASE
    WHEN pk.has_accession = 1 THEN LOWER(pk.chosen_tech) || ' uploaded'
    ELSE 'needs_uploading'
  END AS upload_status
FROM picked pk
LEFT JOIN validated v ON v.og_id = pk.og_id
ORDER BY pk.og_id
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

# --- Separate data quality issues report ---
flag_idx = cols.index('data_quality_flag')
flagged_rows = [r for r in rows if r[flag_idx]]

if out_path:
    flag_path = out_path.with_name(f"{out_path.stem}_data_quality_issues{out_path.suffix}")
    with open(flag_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(flagged_rows)
    print(f"\nData quality issues ({len(flagged_rows)} rows) written to {flag_path}")
else:
    print(f"\nData quality issues: {len(flagged_rows)} rows")
    if flagged_rows:
        print('\t'.join(cols))
        for row in flagged_rows:
            print('\t'.join('' if x is None else str(x) for x in row))

conn.close()
