"""Shared SQL queries for the NCBI upload status reports.

Single source of truth for the draft/ref/mito status queries, imported by
the three standalone scripts (draft_genome_upload_status.py,
ref_genome_upload_status.py, mitogenome_upload_status.py) and by
combined_upload_status.py, so the logic only ever lives in one place.
"""

DRAFT_SQL = """
/* -----------------------------------------------------------
   Draft genome upload status

   Scope: one row per og_id, matched to a sample record. For
   og_ids with multiple draft_genomes entries, prioritizes the
   row whose seq_date has a matching ilmn lca_validation record;
   falls back to the latest seq_date only if none of the OG's
   rows have a matching validation.

   Upload status logic:
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
WITH validation_match AS (
    SELECT DISTINCT dg.og_id, dg.seq_date
    FROM draft_genomes dg
    WHERE EXISTS (
        SELECT 1 FROM lca_validation lv
        WHERE lv.og_id = dg.og_id AND lv.seq_date = dg.seq_date AND lv.tech = 'ilmn'
          AND lv.validated_species_name IS NOT NULL AND lv.validated_species_name <> ''
    )
),
latest AS (
    SELECT DISTINCT ON (dg.og_id) dg.*
    FROM draft_genomes dg
    LEFT JOIN validation_match vm ON vm.og_id = dg.og_id AND vm.seq_date = dg.seq_date
    ORDER BY dg.og_id, (vm.seq_date IS NOT NULL) DESC, dg.seq_date DESC
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

REF_SQL = """
/* -----------------------------------------------------------
   Reference genome upload status

   Upload status logic:
   - 'all uploaded'          both haplotype assembly accessions
                             (JBXXXX) present AND at least one
                             SRR accession released on NCBI.
   - 'hap1/hap2 uploaded'    assembly accession (JBXXXX) present
                             in ref_genomes_assembly_uploads.
   - 'hap1/hap2 pending'     BioProject registered but no assembly
                             accession yet — data not yet uploaded.
   - 'raw data <status>' /
     'raw <type> <status>,
      raw <type> <status>'   SRR status for the og_id. If all known
                             SRR statuses agree, shown once as
                             'raw data <status>' (e.g. released). If
                             they differ across data types, each is
                             listed separately prefixed with its
                             type, e.g. 'raw hic released, raw hifi
                             tobereleased'.
   - 'raw data pending'      rawdata BioProject registered but no
                             SRR accession exists yet.
   - 'not registered on NCBI' no entry in ref_genomes_assembly_uploads
                             AND no value (NULL or empty string) in
                             sample.ncbi_biosample_id,
                             bioproject_id_haplotype_1/2,
                             bioproject_sequencing_data, or
                             ncbi_bioproject_id_lvl_3_hifi.
   - 'registered in sample,
      pending sync'          no entry in ref_genomes_assembly_uploads
                             yet, but one of the sample columns above
                             already holds a value — registration has
                             started upstream but hasn't synced to
                             ref_genomes_assembly_uploads yet.

   data_quality_flag (informational, does not affect upload_status):
   - 'no hifi/hic validation
      matching stage-3 seq_date'  the preferred validated_species_name
                             (from a hifi/hic lca_validation row whose
                             seq_date matches the stage-3 ref_genomes
                             row) wasn't found; fell back to the latest
                             validated record overall, regardless of
                             tech or seq_date.
   - 'species mismatch:
      validated vs nominal'  validated_species_name disagrees with
                             sample.nominal_species_id.
----------------------------------------------------------- */
WITH rg3 AS (
    SELECT DISTINCT og_id, seq_date
    FROM ref_genomes
    WHERE stage = 3
),
matched AS (
    SELECT DISTINCT ON (lv.og_id)
        lv.og_id,
        lv.tech,
        lv.seq_date,
        lv.validated_species_name,
        lv.validator
    FROM lca_validation lv
    JOIN rg3 ON rg3.og_id = lv.og_id AND rg3.seq_date = lv.seq_date
    WHERE lv.tech IN ('hifi', 'hic')
      AND lv.validated_species_name IS NOT NULL
      AND lv.validated_species_name <> ''
    ORDER BY lv.og_id, CASE lv.tech WHEN 'hifi' THEN 0 ELSE 1 END, lv.row_created_on DESC
),
fallback AS (
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
validated AS (
    SELECT
        f.og_id,
        COALESCE(m.tech, f.tech) AS tech,
        COALESCE(m.validated_species_name, f.validated_species_name) AS validated_species_name,
        COALESCE(m.validator, f.validator) AS validator,
        (m.og_id IS NULL) AS used_fallback
    FROM fallback f
    LEFT JOIN matched m ON m.og_id = f.og_id
),
sra_summary AS (
    SELECT
        og_id,
        bool_or(ncbi_status = 'Released') AS any_sra_released,
        COUNT(DISTINCT LOWER(ncbi_status)) AS raw_status_variety,
        STRING_AGG(DISTINCT LOWER(ncbi_status), ', ' ORDER BY LOWER(ncbi_status)) AS raw_data_status,
        STRING_AGG(DISTINCT 'raw ' || data_type || ' ' || LOWER(ncbi_status), ', '
                   ORDER BY 'raw ' || data_type || ' ' || LOWER(ncbi_status)) AS raw_data_status_by_type,
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
    CONCAT_WS(', ',
        CASE WHEN v.used_fallback THEN 'no hifi/hic validation matching stage-3 seq_date' END,
        CASE WHEN v.validated_species_name IS DISTINCT FROM s.nominal_species_id
             THEN 'species mismatch: validated vs nominal' END
    ) AS data_quality_flag,
    CASE
        WHEN u.og_id IS NULL
         AND NULLIF(s.ncbi_biosample_id, '') IS NULL
         AND NULLIF(s.bioproject_id_haplotype_1, '') IS NULL
         AND NULLIF(s.bioproject_id_haplotype_2, '') IS NULL
         AND NULLIF(s.bioproject_sequencing_data, '') IS NULL
         AND NULLIF(s.ncbi_bioproject_id_lvl_3_hifi, '') IS NULL
            THEN 'not registered on NCBI'
        WHEN u.og_id IS NULL
            THEN 'registered in sample, pending sync'
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
                WHEN ss.raw_status_variety > 1        THEN ss.raw_data_status_by_type
                WHEN ss.raw_status_variety = 1        THEN 'raw data ' || ss.raw_data_status
                WHEN u.bioproject_rawdata IS NOT NULL THEN 'raw data pending'
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

MITO_SQL = """
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
