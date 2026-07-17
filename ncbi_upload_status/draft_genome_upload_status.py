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

   Upload status logic (checked top-down, first match wins;
   aws_assm is informational only and no longer gates any status):
   - 'all uploaded'                  biosample_accession, bioproject_accession,
                                      sra_accession, AND assembly_accession all
                                      present and validly formatted.
   - 'raw uploaded, assembly
      pending'                       biosample_accession, bioproject_accession,
                                      AND sra_accession present, but
                                      assembly_accession still NULL.
   - 'raw+assembly needs
      uploading'                     catch-all: covers both "nothing
                                      registered yet" (all four fields NULL)
                                      and any other partial state.

   data_quality_flag (informational, does not affect upload_status):
   - 'no validated species name
      recorded'                     no lca_validation row at all has a
                                      validated_species_name for this og_id.
   - 'no ilmn validation matching
      seq_date'                     validated species name exists, but not
                                      from an ilmn tech row whose seq_date
                                      matches this draft_genomes record; fell
                                      back to the latest validated record
                                      overall, regardless of tech/seq_date.
   - 'species mismatch: validated
      vs nominal'                   validated_species_name disagrees with
                                      sample.nominal_species_id.
----------------------------------------------------------- */
WITH latest AS (
    SELECT DISTINCT ON (og_id) *
    FROM draft_genomes
    ORDER BY og_id, seq_date DESC
),
matched AS (
    SELECT DISTINCT ON (lv.og_id)
        lv.og_id,
        lv.tech,
        lv.validated_species_name
    FROM lca_validation lv
    JOIN latest dg ON dg.og_id = lv.og_id AND dg.seq_date = lv.seq_date
    WHERE lv.tech = 'ilmn'
      AND lv.validated_species_name IS NOT NULL
      AND lv.validated_species_name <> ''
    ORDER BY lv.og_id, lv.row_created_on DESC
),
fallback AS (
    SELECT DISTINCT ON (og_id)
        og_id, tech, validated_species_name
    FROM lca_validation
    WHERE validated_species_name IS NOT NULL
      AND validated_species_name <> ''
    ORDER BY og_id, row_created_on DESC
),
validated AS (
    SELECT
        l.og_id,
        COALESCE(m.validated_species_name, f.validated_species_name) AS validated_species_name,
        (f.og_id IS NULL) AS no_validation,
        (f.og_id IS NOT NULL AND m.og_id IS NULL) AS used_fallback
    FROM latest l
    LEFT JOIN matched m ON m.og_id = l.og_id
    LEFT JOIN fallback f ON f.og_id = l.og_id
)
SELECT
    dg.og_id,
    s.nominal_species_id,
    v.validated_species_name,
    dg.aws_assm,
    dg.biosample_accession,
    dg.bioproject_accession,
    dg.sra_accession,
    dg.assembly_accession,
    s.embargo_status,
    CONCAT_WS(', ',
        CASE WHEN v.no_validation THEN 'no validated species name recorded' END,
        CASE WHEN v.used_fallback THEN 'no ilmn validation matching seq_date' END,
        CASE WHEN NOT v.no_validation
              AND v.validated_species_name IS DISTINCT FROM s.nominal_species_id
             THEN 'species mismatch: validated vs nominal' END
    ) AS data_quality_flag,
    CASE
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.bioproject_accession IS NOT NULL AND dg.bioproject_accession ~* '^PRJ')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^SRR')
       AND NULLIF(dg.assembly_accession, '') IS NOT NULL
        THEN 'all uploaded'
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.bioproject_accession IS NOT NULL AND dg.bioproject_accession ~* '^PRJ')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^SRR')
       AND NULLIF(dg.assembly_accession, '') IS NULL
        THEN 'raw uploaded, assembly pending'
      ELSE 'raw+assembly needs uploading'
    END AS upload_status
FROM latest dg
JOIN sample s ON s.og_id = dg.og_id
LEFT JOIN validated v ON v.og_id = dg.og_id
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
