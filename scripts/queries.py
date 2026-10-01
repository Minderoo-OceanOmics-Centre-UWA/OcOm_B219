"""Shared SQL queries for the NCBI upload status reports.

Single source of truth for the draft/ref/mito status queries, imported by
the three standalone scripts (draft_genome_upload_status.py,
ref_genome_upload_status.py, mitogenome_upload_status.py) and by
combined_upload_status.py, so the logic only ever lives in one place.

Also holds TYPES/CLASSIFY: the shared "which upload_status values count as
actually uploaded" logic, used by both generate_summary_report.py and
action_items_report.py so the definition of "uploaded" only lives here.
"""

TYPES = [
    ('draft', 'Draft genomes'),
    ('ref', 'Reference genomes'),
    ('mito', 'Mitogenomes'),
]


DRAFT_UPLOADED = ('all uploaded', 'submitted, awaiting GCA')
REF_UPLOADED = ('all uploaded', 'all uploaded, public')
MITO_UPLOADED = ('submitted, accession assigned', 'submitted, public')


def classify_draft(s):
    if not s:
        return 'no_record'
    return 'uploaded' if s in DRAFT_UPLOADED else 'in_progress'


def classify_ref(s):
    if not s:
        return 'no_record'
    return 'uploaded' if s in REF_UPLOADED else 'in_progress'


def classify_mito(s):
    if not s:
        return 'no_record'
    return 'uploaded' if s in MITO_UPLOADED else 'in_progress'


CLASSIFY = {'draft': classify_draft, 'ref': classify_ref, 'mito': classify_mito}

# ref_genomes_sra_uploads.ncbi_status carries three vocabularies at once: the NCBI-era
# 'Released'/'ToBeReleased' labels, and a bare holdUntilDate written by the ENA pipeline's
# step 09. An accession existing means the data is submitted; this column is only about
# whether it has gone public yet.
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
    dg.ena_analysis_accession,
    dg.assembly_accession,
    s.embargo_status,
    CONCAT_WS(', ',
        CASE WHEN v.no_validation THEN 'no validated species name recorded' END,
        CASE WHEN v.used_fallback THEN 'no ilmn validation matching seq_date' END,
        CASE WHEN NOT v.no_validation
              AND v.validated_species_name IS DISTINCT FROM s.nominal_species_id
             THEN 'species mismatch: validated vs nominal' END,
        CASE WHEN NULLIF(s.ncbi_id::text, '') IS NULL THEN 'missing ncbi_id' END
    ) AS data_quality_flag,
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
),
stage3_seqdates AS (
    SELECT og_id, MIN(seq_date) AS min_seq_date, MAX(seq_date) AS max_seq_date
    FROM ref_genomes
    WHERE stage = 3
    GROUP BY og_id
),
-- Mirrors ENA-ref step 00: a hifi validation must match a stage-3 seq_date, a hic one
-- need not, because Hi-C is often resequenced long after the assembly.
matched AS (
    SELECT DISTINCT ON (lv.og_id)
        lv.og_id,
        lv.tech,
        lv.seq_date,
        lv.validated_species_name,
        lv.validator
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
        validator
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
        COALESCE(m.validator, f.validator) AS validator,
        (f.og_id IS NULL) AS no_validation,
        (f.og_id IS NOT NULL AND m.og_id IS NULL) AS used_fallback
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
    CONCAT_WS(', ',
        CASE WHEN v.no_validation THEN 'no validated species name recorded' END,
        CASE WHEN v.used_fallback THEN 'no hifi/hic validation for this assembly' END,
        CASE WHEN NOT v.no_validation
              AND v.validated_species_name IS DISTINCT FROM s.nominal_species_id
             THEN 'species mismatch: validated vs nominal' END,
        CASE WHEN sd.min_seq_date IS DISTINCT FROM sd.max_seq_date
             THEN 'hap1/hap2 stage-3 seq_date inconsistent' END
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
      m.genbank_accession,
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
  pk.nominal_species_id,
  pk.tol_id,
  pk.cohort,
  es.ena_analysis_accession,
  es.ena_assembly_accession,
  (NULLIF(BTRIM(es.ena_analysis_accession), '') IS NOT NULL
   OR NULLIF(BTRIM(es.ena_assembly_accession), '') IS NOT NULL) AS assembly_published,
  v.validated_species_name,
  CONCAT_WS(', ',
      CASE WHEN v.no_validation THEN 'no validated species name recorded' END,
      CASE WHEN v.used_fallback THEN 'no ' || LOWER(pk.chosen_tech) || ' validation matching seq_date' END,
      CASE WHEN NOT v.no_validation
            AND v.validated_species_name IS DISTINCT FROM pk.nominal_species_id
           THEN 'species mismatch: validated vs nominal' END,
      CASE WHEN NULLIF(pk.ncbi_id::text, '') IS NULL THEN 'missing ncbi_id' END
  ) AS data_quality_flag,
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

TOLID_SQL = """
WITH any_validated AS (
    SELECT DISTINCT ON (og_id)
        og_id, validated_species_name
    FROM lca_validation
    WHERE validated_species_name IS NOT NULL AND validated_species_name <> ''
    ORDER BY og_id, row_created_on DESC
),
hifi_validated AS (
    SELECT DISTINCT ON (og_id)
        og_id, validated_species_name
    FROM lca_validation
    WHERE tech = 'hifi'
      AND validated_species_name IS NOT NULL AND validated_species_name <> ''
    ORDER BY og_id, row_created_on DESC
)
SELECT
    s.og_id,
    s.nominal_species_id,
    s.embargo_status,
    av.validated_species_name,
    hv.validated_species_name AS hifi_validated_species_name,
    s.ncbi_id,
    s.tol_id,
    CASE
        WHEN av.validated_species_name IS NULL THEN 'needs species validation'
        WHEN NULLIF(s.ncbi_id, '') IS NULL THEN 'needs NCBI taxon ID'
        WHEN s.ncbi_id !~ '^[0-9]+(\.0)?$' THEN 'NCBI taxon ID not exist'
        WHEN hv.validated_species_name IS NOT NULL AND NULLIF(s.tol_id, '') IS NULL
            THEN 'needs ToLID request'
    END AS action_needed
FROM sample s
LEFT JOIN any_validated av ON av.og_id = s.og_id
LEFT JOIN hifi_validated hv ON hv.og_id = s.og_id
WHERE s.hifi = 'Y'
ORDER BY s.og_id
"""
