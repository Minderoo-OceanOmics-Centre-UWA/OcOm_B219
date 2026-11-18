"""Shared SQL and status logic for the upload status reports.

Single source of truth: the four queries and the "which upload_status values count
as uploaded" rules, imported by every other script.
"""

TYPES = [
    ('draft', 'Draft genomes'),
    ('ref', 'Reference genomes'),
    ('mito', 'Mitogenomes'),
]


UPLOADED = {
    'draft': ('all uploaded', 'submitted, awaiting GCA'),
    'ref': ('all uploaded', 'all uploaded, public'),
    'mito': ('submitted, accession assigned', 'submitted, public'),
}


def classify(genome_type, status):
    if not status:
        return 'no_record'
    return 'uploaded' if status in UPLOADED[genome_type] else 'in_progress'

RELEASE_STATE_SQL = """
        CASE
            WHEN NULLIF(BTRIM(ncbi_status), '') IS NULL THEN 'unknown'
            WHEN ncbi_status = 'Released' THEN 'public'
            WHEN ncbi_status = 'ToBeReleased' THEN 'embargoed'
            WHEN ncbi_status ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                THEN CASE
                        WHEN TO_DATE(SUBSTRING(ncbi_status FROM 1 FOR 10), 'YYYY-MM-DD') <= CURRENT_DATE
                            THEN 'public'
                        ELSE 'embargoed'
                     END
            ELSE 'unknown'
        END
"""

DRAFT_SQL = """
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
    ORDER BY dg.og_id,
             (NULLIF(dg.biosample_accession, '')      IS NOT NULL
              OR NULLIF(dg.sra_accession, '')         IS NOT NULL
              OR NULLIF(dg.ena_analysis_accession, '') IS NOT NULL
              OR NULLIF(dg.assembly_accession, '')     IS NOT NULL) DESC,
             (vm.seq_date IS NOT NULL) DESC,
             (CASE WHEN dg.seq_date ~ '^[0-9]{6}$'
                   THEN TO_DATE(dg.seq_date, 'YYMMDD') END) DESC NULLS LAST,
             dg.seq_date DESC
),
matched AS (
    SELECT DISTINCT ON (lv.og_id)
        lv.og_id,
        lv.validated_species_name,
        lv.validator,
        lv.validator_2
    FROM lca_validation lv
    JOIN latest dg ON dg.og_id = lv.og_id AND dg.seq_date = lv.seq_date
    WHERE lv.tech = 'ilmn'
      AND lv.validated_species_name IS NOT NULL
      AND lv.validated_species_name <> ''
    ORDER BY lv.og_id, lv.row_created_on DESC
),
fallback AS (
    SELECT DISTINCT ON (og_id)
        og_id, validated_species_name, validator, validator_2
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
        (f.og_id IS NOT NULL AND m.og_id IS NULL) AS used_fallback,
        (NULLIF(BTRIM(COALESCE(m.validator, f.validator)::text), '')   IS NOT NULL) AS has_validator,
        (NULLIF(BTRIM(COALESCE(m.validator_2, f.validator_2)::text), '') IS NOT NULL) AS has_validator_2
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
    dg.ena_analysis_accession,
    dg.assembly_accession,
    s.embargo_status,
    CASE
        WHEN v.no_validation
          OR NOT COALESCE(v.has_validator, false)
          OR NOT COALESCE(v.has_validator_2, false)
            THEN 'species name not validated'
        WHEN v.used_fallback
            THEN 'validation not for this assembly'
        WHEN NULLIF(s.ncbi_id::text, '') IS NULL
          OR s.ncbi_id::text !~ '^[0-9]+(\.0)?$'
            THEN 'missing taxon ID'
    END AS blocking_action,
    (dg.sra_accession IS NOT NULL AND dg.sra_accession ~* '^(SRR|ERR)') AS raw_submitted,
    (NULLIF(dg.ena_analysis_accession, '') IS NOT NULL
     OR NULLIF(dg.assembly_accession, '') IS NOT NULL) AS assembly_published,
    CASE
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^(SRR|ERR)')
       AND NULLIF(dg.assembly_accession, '') IS NOT NULL
        THEN 'all uploaded'
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^(SRR|ERR)')
       AND NULLIF(dg.ena_analysis_accession, '') IS NOT NULL
        THEN 'submitted, awaiting GCA'
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^(SRR|ERR)')
        THEN 'raw uploaded, assembly pending'
      ELSE 'raw+assembly needs uploading'
    END AS upload_status
FROM latest dg
JOIN sample s ON s.og_id = dg.og_id
LEFT JOIN validated v ON v.og_id = dg.og_id
ORDER BY dg.og_id
"""

REF_SQL = """
WITH rg3 AS (
    SELECT DISTINCT og_id, seq_date
    FROM ref_genomes
    WHERE stage = 3
),
stage3_ogs AS (
    SELECT DISTINCT og_id FROM ref_genomes WHERE stage = 3
    UNION
    SELECT DISTINCT og_id FROM ref_genomes_assembly_uploads
),
stage3_seqdates AS (
    SELECT og_id, MIN(seq_date) AS min_seq_date, MAX(seq_date) AS max_seq_date
    FROM ref_genomes
    WHERE stage = 3
    GROUP BY og_id
),
matched AS (
    SELECT DISTINCT ON (lv.og_id)
        lv.og_id,
        lv.tech,
        lv.validated_species_name,
        lv.validator,
        lv.validator_2
    FROM lca_validation lv
    WHERE lv.tech IN ('hifi', 'hic')
      AND lv.validated_species_name IS NOT NULL
      AND lv.validated_species_name <> ''
      AND EXISTS (
          SELECT 1 FROM rg3
          WHERE rg3.og_id = lv.og_id
            AND (lv.tech = 'hic' OR rg3.seq_date = lv.seq_date)
      )
    ORDER BY lv.og_id, CASE lv.tech WHEN 'hifi' THEN 0 ELSE 1 END, lv.row_created_on DESC
),
fallback AS (
    SELECT DISTINCT ON (og_id)
        og_id,
        tech,
        validated_species_name,
        validator,
        validator_2
    FROM lca_validation
    WHERE validated_species_name IS NOT NULL
      AND validated_species_name <> ''
    ORDER BY og_id, row_created_on DESC
),
validated AS (
    SELECT
        o.og_id,
        COALESCE(m.tech, f.tech) AS tech,
        COALESCE(m.validated_species_name, f.validated_species_name) AS validated_species_name,
        (f.og_id IS NULL) AS no_validation,
        (f.og_id IS NOT NULL AND m.og_id IS NULL) AS used_fallback,
        (NULLIF(BTRIM(COALESCE(m.validator, f.validator)::text), '')     IS NOT NULL) AS has_validator,
        (NULLIF(BTRIM(COALESCE(m.validator_2, f.validator_2)::text), '') IS NOT NULL) AS has_validator_2
    FROM stage3_ogs o
    LEFT JOIN matched m ON m.og_id = o.og_id
    LEFT JOIN fallback f ON f.og_id = o.og_id
),
sra_norm AS (
    SELECT
        og_id,
        COALESCE(NULLIF(LOWER(BTRIM(data_type)), ''), 'unknown') AS data_type,
        srr_accession,
        ncbi_status,
""" + RELEASE_STATE_SQL + """ AS release_state
    FROM ref_genomes_sra_uploads
),
sra_summary AS (
    SELECT
        og_id,
        bool_or(release_state = 'public') AS any_raw_public,
        bool_and(release_state = 'public') AS all_raw_public,
        bool_or(NULLIF(BTRIM(srr_accession), '') IS NOT NULL) AS raw_submitted,
        COUNT(DISTINCT release_state) AS raw_status_variety,
        STRING_AGG(DISTINCT release_state, ', ' ORDER BY release_state) AS raw_data_status,
        STRING_AGG(DISTINCT 'raw ' || data_type || ' ' || release_state, ', '
                   ORDER BY 'raw ' || data_type || ' ' || release_state) AS raw_data_status_by_type,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'hifi')     AS hifi_srr,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'hic')      AS hic_srr,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'ont')      AS ont_srr,
        STRING_AGG(srr_accession, ', ') FILTER (WHERE data_type = 'illumina') AS illumina_srr,
        STRING_AGG(DISTINCT release_state, '+' ORDER BY release_state) FILTER (WHERE data_type = 'hifi')     AS hifi_release,
        STRING_AGG(DISTINCT release_state, '+' ORDER BY release_state) FILTER (WHERE data_type = 'hic')      AS hic_release,
        STRING_AGG(DISTINCT release_state, '+' ORDER BY release_state) FILTER (WHERE data_type = 'ont')      AS ont_release,
        STRING_AGG(DISTINCT release_state, '+' ORDER BY release_state) FILTER (WHERE data_type = 'illumina') AS illumina_release,
        STRING_AGG(DISTINCT release_state, '+' ORDER BY release_state) FILTER (WHERE data_type = 'unknown')  AS unknown_release
    FROM sra_norm
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
    u.gca_accession_hap1,
    u.bioproject_hap2,
    u.assembly_accession_hap2,
    u.gca_accession_hap2,
    u.bioproject_rawdata,
    ss.hifi_srr,
    ss.hifi_release,
    ss.hic_srr,
    ss.hic_release,
    ss.ont_srr,
    ss.ont_release,
    ss.illumina_srr,
    ss.illumina_release,
    ss.unknown_release,
    COALESCE(ss.raw_submitted, false) AS raw_submitted,
    (u.assembly_accession_hap1 IS NOT NULL
     OR u.assembly_accession_hap2 IS NOT NULL
     OR NULLIF(u.gca_accession_hap1, '') IS NOT NULL
     OR NULLIF(u.gca_accession_hap2, '') IS NOT NULL) AS assembly_published,
    COALESCE(u.embargo_status, s.embargo_status) AS embargo_status,
    CASE
        WHEN v.no_validation
          OR NOT COALESCE(v.has_validator, false)
          OR NOT COALESCE(v.has_validator_2, false)
            THEN 'species name not validated'
        WHEN v.used_fallback
            THEN 'validation not for this assembly'
        WHEN sd.min_seq_date IS DISTINCT FROM sd.max_seq_date
            THEN 'hap1/hap2 seq_date inconsistent'
        WHEN NULLIF(s.ncbi_id::text, '') IS NULL
          OR s.ncbi_id::text !~ '^[0-9]+(\.0)?$'
            THEN 'missing taxon ID'
        WHEN NULLIF(BTRIM(s.tol_id), '') IS NULL
          OR s.tol_id !~ '^[a-z][A-Za-z]{6}[0-9]+$'
            THEN 'missing ToLID'
    END AS blocking_action,
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
        WHEN NULLIF(u.gca_accession_hap1, '') IS NOT NULL
         AND NULLIF(u.gca_accession_hap2, '') IS NOT NULL
         AND COALESCE(ss.raw_submitted, false)
            THEN 'all uploaded, public'
        WHEN u.assembly_accession_hap1 IS NOT NULL
         AND u.assembly_accession_hap2 IS NOT NULL
         AND COALESCE(ss.raw_submitted, false)
            THEN 'all uploaded'
        ELSE CONCAT_WS(', ',
            CASE
                WHEN NULLIF(u.gca_accession_hap1, '') IS NOT NULL THEN 'hap1 public'
                WHEN u.assembly_accession_hap1        IS NOT NULL THEN 'hap1 uploaded'
                WHEN u.bioproject_hap1                IS NOT NULL THEN 'hap1 pending'
            END,
            CASE
                WHEN NULLIF(u.gca_accession_hap2, '') IS NOT NULL THEN 'hap2 public'
                WHEN u.assembly_accession_hap2        IS NOT NULL THEN 'hap2 uploaded'
                WHEN u.bioproject_hap2                IS NOT NULL THEN 'hap2 pending'
            END,
            CASE
                WHEN ss.raw_status_variety > 1        THEN ss.raw_data_status_by_type
                WHEN ss.raw_status_variety = 1        THEN 'raw data ' || ss.raw_data_status
                WHEN u.bioproject_rawdata IS NOT NULL THEN 'raw data pending'
                ELSE 'raw data not submitted'
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
LEFT JOIN stage3_seqdates sd
    ON sd.og_id = v.og_id
ORDER BY v.og_id
"""

MITO_SQL = """
WITH base AS (
  SELECT
      m.og_num,
      m.og_id,
      m.tech,
      m.seq_date,
      m.code,
      m.annotation,
      s.embargo_status,
      s.nominal_species_id,
      s.ncbi_id,
      s.tol_id,
      CASE
        WHEN EXISTS (SELECT 1 FROM ref_genomes r WHERE r.og_id = m.og_id AND r.stage = 3)
          THEN 'reference'
        WHEN EXISTS (SELECT 1 FROM draft_genomes d WHERE d.og_id = m.og_id)
          THEN 'draft'
        ELSE 'unknown'
      END AS cohort,
      CASE
        WHEN m.seq_date ~ '^[0-9]{6}$' THEN TO_DATE(m.seq_date, 'YYMMDD')
        ELSE NULL
      END AS seq_date_dt,
      EXISTS (
        SELECT 1 FROM ena_submissions es
        WHERE es.og_id = m.og_id AND es.tech = m.tech AND es.seq_date = m.seq_date
          AND es.code = m.code AND es.annotation = m.annotation
          AND es.webin_mode = 'production'
      ) AS has_submission
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
        ORDER BY b.has_submission DESC,
                 b.seq_date_dt DESC NULLS LAST, b.seq_date DESC, b.code DESC
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
    MAX(CASE WHEN LOWER(tech) = 'ilmn' THEN 1 ELSE 0 END) AS has_ilmn,
    MAX(CASE WHEN LOWER(tech) = 'hifi' AND has_submission THEN 1 ELSE 0 END) AS sub_hifi,
    MAX(CASE WHEN LOWER(tech) = 'hic'  AND has_submission THEN 1 ELSE 0 END) AS sub_hic,
    MAX(CASE WHEN LOWER(tech) = 'ilmn' AND has_submission THEN 1 ELSE 0 END) AS sub_ilmn
  FROM dedup
  GROUP BY og_id
),
chosen AS (
  SELECT
    d.*,
    CASE
      WHEN f.sub_hifi = 1 THEN 'hifi'
      WHEN f.sub_hic  = 1 THEN 'hic'
      WHEN f.sub_ilmn = 1 THEN 'ilmn'
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
      lv.validated_species_name,
      lv.validator,
      lv.validator_2
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
      og_id, validated_species_name, validator, validator_2
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
      (f.og_id IS NOT NULL AND m.og_id IS NULL) AS used_fallback,
      (NULLIF(BTRIM(COALESCE(m.validator, f.validator)::text), '')     IS NOT NULL) AS has_validator,
      (NULLIF(BTRIM(COALESCE(m.validator_2, f.validator_2)::text), '') IS NOT NULL) AS has_validator_2
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
  pk.embargo_status,
  pk.nominal_species_id,
  pk.tol_id,
  pk.cohort,
  es.ena_analysis_accession,
  es.ena_assembly_accession,
  (NULLIF(BTRIM(es.ena_analysis_accession), '') IS NOT NULL
   OR NULLIF(BTRIM(es.ena_assembly_accession), '') IS NOT NULL) AS assembly_published,
  v.validated_species_name,
  CASE
    WHEN v.no_validation
      OR NOT COALESCE(v.has_validator, false)
      OR NOT COALESCE(v.has_validator_2, false)
        THEN 'species name not validated'
    WHEN v.used_fallback
        THEN 'validation not for this assembly'
    WHEN NOT COALESCE(ev.submission_ready, false)
        THEN 'flatfile not validated for ENA'
    WHEN NULLIF(pk.ncbi_id::text, '') IS NULL
      OR pk.ncbi_id::text !~ '^[0-9]+(\.0)?$'
        THEN 'missing taxon ID'
  END AS blocking_action,
  CASE
    WHEN NULLIF(BTRIM(es.ena_assembly_accession), '') IS NOT NULL THEN 'submitted, public'
    WHEN es.submission_status = 'ACCESSION_ASSIGNED' THEN 'submitted, accession assigned'
    WHEN es.submission_status = 'FAILED' THEN 'submission failed'
    WHEN es.submission_status = 'NOT_SUBMITTED' THEN 'not submitted'
    WHEN ev.submission_ready THEN 'ready to submit'
    ELSE 'needs uploading'
  END AS upload_status
FROM picked pk
LEFT JOIN validated v ON v.og_id = pk.og_id
LEFT JOIN ena_validation_latest ev
  ON ev.og_id = pk.og_id AND ev.tech = pk.tech AND ev.seq_date = pk.seq_date
 AND ev.code = pk.code AND ev.annotation = pk.annotation
LEFT JOIN ena_submissions es
  ON es.og_id = pk.og_id AND es.tech = pk.tech AND es.seq_date = pk.seq_date
 AND es.code = pk.code AND es.annotation = pk.annotation AND es.webin_mode = 'production'
ORDER BY pk.og_id
"""
