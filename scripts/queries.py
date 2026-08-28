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


def classify_draft(s):
    if not s:
        return 'no_record'
    return 'uploaded' if s == 'all uploaded' else 'in_progress'


def classify_ref(s):
    if not s:
        return 'no_record'
    return 'uploaded' if s == 'all uploaded' else 'in_progress'


def classify_mito(s):
    if not s:
        return 'no_record'
    return 'uploaded' if s == 'submitted, accession assigned' else 'in_progress'


CLASSIFY = {'draft': classify_draft, 'ref': classify_ref, 'mito': classify_mito}

DRAFT_SQL = """
/* -----------------------------------------------------------
   Draft genome upload status

   Scope: one row per og_id, matched to a sample record. For
   og_ids with multiple draft_genomes entries, prioritizes the
   row whose seq_date has a matching ilmn lca_validation record;
   falls back to the latest seq_date only if none of the OG's
   rows have a matching validation.

   Upload status logic (bioproject_accession is NOT checked -- per
   ENA-draft-genomes' own comment, draft's two BioProjects are fixed
   constants, never written back per-OG, so requiring one here would be a
   stricter check than the real pipeline and produce false negatives):
   - 'all uploaded'                  biosample_accession, sra_accession, AND
                                      assembly_accession all present and
                                      validly formatted.
   - 'raw uploaded, assembly
      pending'                       biosample_accession AND sra_accession
                                      present, but assembly_accession still
                                      NULL.
   - 'raw+assembly needs
      uploading'                     catch-all: covers both "nothing
                                      registered yet" (all four fields NULL)
                                      and any other partial state.

   data_quality_flag (informational, does not affect upload_status; matches
   ENA-draft-genomes/00_create_draft_samplesheet.py's own checks, so an OG
   flagged clear here should pass that script's validation too):
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
   - 'missing ncbi_id'              sample.ncbi_id is blank -- ENA's
                                      mandatory TAXON_ID, a hard gate on
                                      that pipeline's side (no BioSample/
                                      BioProject possible without it).
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
             THEN 'species mismatch: validated vs nominal' END,
        CASE WHEN NULLIF(s.ncbi_id::text, '') IS NULL THEN 'missing ncbi_id' END
    ) AS data_quality_flag,
    CASE
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^(SRR|ERR)')
       AND NULLIF(dg.assembly_accession, '') IS NOT NULL
        THEN 'all uploaded'
      WHEN (dg.biosample_accession IS NOT NULL AND dg.biosample_accession ~* '^SAM')
       AND (dg.sra_accession        IS NOT NULL AND dg.sra_accession        ~* '^(SRR|ERR)')
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

   data_quality_flag (informational, does not affect upload_status; matches
   ENA-ref-genomes/00_pull_ready_to_upload_samples.py's own checks -- note
   that script treats several of these as HARD exclusions rather than just
   flags, so an OG flagged here may be rejected there even though it still
   shows up in this report):
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
   - 'validated via hic only
      (ENA pipeline requires
      hifi)'                  the matched validation came from a hic row,
                             not hifi -- that pipeline's own query only
                             ever accepts tech='hifi', no hic fallback,
                             so it would reject this OG even though this
                             report treats hic as acceptable evidence.
   - 'hap1/hap2 stage-3
      seq_date inconsistent' the OG's stage-3 ref_genomes rows don't all
                             share the same seq_date -- that pipeline
                             hard-excludes on this (stage3_seq_date_
                             inconsistent); here it just means the exact-
                             match check above may have silently matched
                             whichever seq_date happened to line up.
----------------------------------------------------------- */
WITH rg3 AS (
    SELECT DISTINCT og_id, seq_date
    FROM ref_genomes
    WHERE stage = 3
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
             THEN 'species mismatch: validated vs nominal' END,
        CASE WHEN NOT v.used_fallback AND v.tech = 'hic'
             THEN 'validated via hic only (ENA pipeline requires hifi)' END,
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
LEFT JOIN stage3_seqdates sd
    ON sd.og_id = v.og_id
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

   Upload status logic (Nov 2026): based on the Oceanomics-OceanGenomes-
   Mitogenomes pipeline's actual current output, NOT mitogenome_data
   .genbank_accession -- confirmed by reading that pipeline's source that no
   code path writes that column any more (it predates the pipeline; the
   column is being phased out in favour of the tables below, per that
   pipeline's own docs/ena_submission_handoff.md).
   - 'submitted, accession
      assigned'          ena_submissions.submission_status = 'ACCESSION_ASSIGNED'
                         for this exact assembly+annotation (production mode)
                         -- an ENA GCA/OU/LR accession exists.
   - 'submitted, pending
      accession'         ena_submissions.submission_status = 'SUBMITTED' --
                         sent to ENA, accession not back yet.
   - 'submission failed'  ena_submissions.submission_status = 'FAILED'.
   - 'ready to submit'    no ena_submissions row yet, but the flatfile has
                         cleared every gate the Mitogenomes pipeline runs
                         (table2asn + EMBL conversion + Webin format
                         validation) -- ena_validation_attempts
                         .submission_ready = true for this exact
                         assembly+annotation.
   - 'needs_uploading'   catch-all: assembly/annotation exists but hasn't
                         reached submission_ready yet (still being
                         assembled, annotated, or QC'd).
   Note: submission_ready is a FORMAT check only -- it says nothing about
   species validation, which is tracked separately below.

   data_quality_flag (informational, does not affect upload_status; matches
   ENA-mito-genomes/00_create_mito_samplesheet.py's own not-ready reasons
   where applicable):
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
   - 'missing ncbi_id'   sample.ncbi_id is blank -- that pipeline's own
                         "missing ncbi_id" not-ready reason (no BioSample
                         can be created without it). Checked here
                         independently of TOLID_SQL, since mitogenomes
                         assembled via hic/ilmn (not hifi) never appear
                         in TOLID_SQL's sample.hifi='Y' population.
----------------------------------------------------------- */
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
    WHEN es.submission_status = 'ACCESSION_ASSIGNED' THEN 'submitted, accession assigned'
    WHEN es.submission_status = 'SUBMITTED' THEN 'submitted, pending accession'
    WHEN es.submission_status = 'FAILED' THEN 'submission failed'
    WHEN ev.submission_ready THEN 'ready to submit'
    ELSE 'needs_uploading'
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
/* -----------------------------------------------------------
   ToLID / NCBI taxon readiness (OcOm_SOP_B231 pre-check)

   Scope: every OG whose sample underwent HiFi sequencing
   (sample.hifi = 'Y'), regardless of ref_genomes.stage. This is
   deliberately broader than REF_SQL's stage-3-only scope, since
   B231's own ToLID request trigger is HiFi-sequenced + validated,
   not "already assembled" -- REF_SQL alone would miss pre-stage-3
   candidates.

   action_needed logic (first match wins; NULL if fully ready):
   - 'needs species validation'  no lca_validation row (any tech)
                                  has a validated_species_name for
                                  this og_id yet.
   - 'needs NCBI taxon ID'       a validated species name exists
                                  (any tech), but sample.ncbi_id is
                                  blank -- hasn't been looked up yet.
   - 'NCBI taxon ID not exist'   sample.ncbi_id holds a non-numeric
                                  placeholder (e.g. the literal
                                  'not found') -- it WAS looked up,
                                  but NCBI Taxonomy has no entry for
                                  this species, which needs different
                                  follow-up than a plain lookup.
   - 'needs ToLID request'       a HiFi-tech validated species name
                                  exists specifically (matching
                                  B231's HiFi-specific trigger) and
                                  ncbi_id is a real numeric taxon ID,
                                  but sample.tol_id is still blank.

   Note: two-validator sign-off (lca_validation.validator /
   validator_2) is intentionally NOT checked here -- OcOm_SOP_B231's
   own procedure already confirms sign-off as part of actually
   submitting the request, so pre-flagging it here would just
   duplicate that check.
----------------------------------------------------------- */
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
