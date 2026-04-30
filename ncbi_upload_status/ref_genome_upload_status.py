#!/usr/bin/env python3
"""Reference genome upload status report.

Usage:
    singularity run $SING/psycopg2:0.1.sif python ref_genome_upload_status.py \
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
WITH validated AS (
    SELECT DISTINCT ON (og_id)
        og_id,
        tech,
        validated_species_name,
        validator
    FROM lca_validation
    WHERE validated_species_name IS NOT NULL
      AND validated_species_name <> ''
    ORDER BY og_id, row_created_on DESC
),
sra_summary AS (
    SELECT
        og_id,
        bool_or(ncbi_status = 'Released') AS any_sra_released,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'hifi')     AS hifi_srr,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'hic')      AS hic_srr,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'ont')      AS ont_srr,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'illumina') AS illumina_srr,
        STRING_AGG(ncbi_status,   ', ') FILTER (WHERE data_type = 'hifi')     AS hifi_status,
        STRING_AGG(ncbi_status,   ', ') FILTER (WHERE data_type = 'hic')      AS hic_status,
        STRING_AGG(ncbi_status,   ', ') FILTER (WHERE data_type = 'ont')      AS ont_status,
        STRING_AGG(ncbi_status,   ', ') FILTER (WHERE data_type = 'illumina') AS illumina_status
    FROM ref_genomes_sra_uploads
    GROUP BY og_id
)
SELECT
    v.og_id,
    v.tech,
    v.validated_species_name,
    s.nominal_species_id,
    s.common_name,
    s.tol_id,
    s.ncbi_id,
    s.workflow,
    u.biosample,
    u.bioproject_umbrella,
    u.bioproject_hap1,
    u.assembly_accession_hap1,
    u.bioproject_hap2,
    u.assembly_accession_hap2,
    u.bioproject_rawdata,
    ss.hifi_srr,
    ss.hifi_status,
    ss.hic_srr,
    ss.hic_status,
    ss.ont_srr,
    ss.ont_status,
    ss.illumina_srr,
    ss.illumina_status,
    COALESCE(u.embargo_status, s.embargo_status) AS embargo_status,
    CASE
        WHEN u.og_id IS NULL
            THEN 'not registered on NCBI'
        WHEN u.assembly_accession_hap1 IS NOT NULL
         AND u.assembly_accession_hap2 IS NOT NULL
         AND COALESCE(ss.any_sra_released, false)
            THEN 'all uploaded'
        ELSE CONCAT_WS(', ',
            CASE
                WHEN u.assembly_accession_hap1 IS NOT NULL THEN 'hap1 uploaded'
                WHEN u.bioproject_hap1         IS NOT NULL THEN 'hap1 pending'
            END,
            CASE
                WHEN u.assembly_accession_hap2 IS NOT NULL THEN 'hap2 uploaded'
                WHEN u.bioproject_hap2         IS NOT NULL THEN 'hap2 pending'
            END,
            CASE
                WHEN COALESCE(ss.any_sra_released, false)  THEN 'raw data released'
                WHEN u.bioproject_rawdata IS NOT NULL       THEN 'raw data pending'
            END
        )
    END AS upload_status
FROM validated v
INNER JOIN sample s
    ON s.og_id = v.og_id
LEFT JOIN ref_genomes_assembly_uploads u
    ON u.og_id = v.og_id
LEFT JOIN sra_summary ss
    ON ss.og_id = v.og_id
WHERE EXISTS (
    SELECT 1 FROM ref_genomes r
    WHERE r.og_id = v.og_id AND r.stage = 3
)
ORDER BY v.og_id
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
