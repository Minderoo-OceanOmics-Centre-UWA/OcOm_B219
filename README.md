# NCBI Upload Status Scripts

Scripts for reporting NCBI submission status across the three OceanGenomes genome types: reference genomes, draft genomes, and mitogenomes.

Each script queries the OceanOmics PostgreSQL database directly, bypassing the SQL client paste-length limitation that affects long queries in DBeaver.

---

## Requirements

- Singularity container: `$SING/psycopg2:0.1.sif`
- PostgreSQL credentials: `~/postgresql_details/oceanomics.cfg`

---

## Usage

All three scripts share the same interface:

```bash
singularity run $SING/psycopg2:0.1.sif python <script.py> \
    ~/postgresql_details/oceanomics.cfg [output.csv]
```

- Without `output.csv`: prints tab-separated results to stdout with a summary, followed by a separate "data quality issues" section (rows with a non-empty `data_quality_flag`).
- With `output.csv`: writes a CSV file and prints the summary. Also writes a companion `<name>_data_quality_issues.csv` alongside it, containing only the flagged rows.

---

## Scripts

### `ref_genome_upload_status.py`

Reports NCBI upload status for reference genomes at assembly stage 3.

**Filters:** OGs with a validated species name in `lca_validation` (deduplicated to the most recent record per OG) and a stage 3 entry in `ref_genomes`.

**Upload status values:**

`upload_status` is either one of two fixed values, or a composite string built from up to three parts (hap1 status, hap2 status, raw data status) joined with `, `:

| Value | Meaning |
|-------|---------|
| `not registered on NCBI` | No entry in `ref_genomes_assembly_uploads`, and none of sample's `ncbi_biosample_id`, `bioproject_id_haplotype_1/2`, `bioproject_sequencing_data`, `ncbi_bioproject_id_lvl_3_hifi` hold a value |
| `registered in sample, pending sync` | No entry in `ref_genomes_assembly_uploads` yet, but one of the sample columns above already holds a value (registration started upstream but hasn't synced down) |
| `all uploaded` | Both haplotype assembly accessions (JBXXXX) present AND at least one SRR released |
| `hap1 uploaded` / `hap1 pending` | Hap1 GenBank assembly accession assigned / BioProject registered but no assembly accession yet |
| `hap2 uploaded` / `hap2 pending` | Hap2 GenBank assembly accession assigned / BioProject registered but no assembly accession yet |
| `raw data <status>` | All known SRR statuses for the OG agree, e.g. `raw data released` |
| `raw <type> <status>, raw <type> <status>` | SRR statuses differ across data types, each listed separately, e.g. `raw hic released, raw hifi tobereleased` |
| `raw data pending` | Rawdata BioProject registered but no SRR accession exists yet |

**Data quality flag** (informational, independent of `upload_status`; matches `lca_validation` against the stage-3 `seq_date`, preferring hifi over hic):

| Value | Meaning |
|-------|---------|
| `no hifi/hic validation matching stage-3 seq_date` | No hifi/hic `lca_validation` row matches this OG's stage-3 `seq_date` — fell back to the latest validated record overall, regardless of tech/seq_date |
| `species mismatch: validated vs nominal` | `validated_species_name` disagrees with `sample.nominal_species_id` |

---

### `draft_genome_upload_status.py`

Reports NCBI upload status for draft genomes, one row per `og_id` (using the latest `seq_date` for OGs with more than one `draft_genomes` record).

**Filters:** none — every `og_id` in `draft_genomes` that also has a matching `sample` row is included.

**Upload status values** (checked top-down, first match wins; the first three all require `aws_assm` to be present):

| Value | Meaning |
|-------|---------|
| `all uploaded` | `biosample_accession`, `bioproject_accession`, `sra_accession`, AND `assembly_accession` all present and validly formatted |
| `raw uploaded, assembly pending` | `biosample_accession`, `bioproject_accession`, AND `sra_accession` present, but `assembly_accession` still NULL |
| `raw+assembly needs uploading` | Assembly file ready (`aws_assm` present) but `biosample_accession`, `bioproject_accession`, `sra_accession`, AND `assembly_accession` are all NULL — nothing registered yet |
| `missing .fna or fields` | Catch-all: `aws_assm` is NULL (no assembly file yet), or any other partial state not covered above |

**Data quality flag** (informational, independent of `upload_status`; matches `lca_validation` against this record's `seq_date`, tech = ilmn):

| Value | Meaning |
|-------|---------|
| `no validated species name recorded` | No `lca_validation` row at all has a validated species name for this OG |
| `no ilmn validation matching seq_date` | A validated species name exists, but not from an ilmn-tech row matching this `draft_genomes` `seq_date` — fell back to the latest validated record overall |
| `species mismatch: validated vs nominal` | `validated_species_name` disagrees with `sample.nominal_species_id` |

---

### `mitogenome_upload_status.py`

Reports GenBank upload status for mitogenomes, one row per OG using the best available sequencing technology (priority: hifi > hic > ilmn).

**Filters:**
- Excludes `_concat` OG IDs.
- Deduplicates to the most recent record per OG + technology.
- Selects one technology per OG by priority.

**Upload status values:**

| Value | Meaning |
|-------|---------|
| `hifi uploaded` | GenBank accession present, assembled from HiFi |
| `hic uploaded` | GenBank accession present, assembled from Hi-C |
| `ilmn uploaded` | GenBank accession present, assembled from Illumina |
| `needs_uploading` | No GenBank accession recorded |

**Data quality flag** (informational, independent of `upload_status`; matches `lca_validation` against this record's `seq_date` and its chosen technology):

| Value | Meaning |
|-------|---------|
| `no validated species name recorded` | No `lca_validation` row at all has a validated species name for this OG |
| `no <tech> validation matching seq_date` | A validated species name exists, but not from a row matching the OG's chosen technology AND `seq_date` — fell back to the latest validated record overall |
| `species mismatch: validated vs nominal` | `validated_species_name` disagrees with `sample.nominal_species_id` |

---

## Example

```bash
# Print to terminal
singularity run $SING/psycopg2:0.1.sif python ref_genome_upload_status.py \
    ~/postgresql_details/oceanomics.cfg

# Save to CSV
singularity run $SING/psycopg2:0.1.sif python mitogenome_upload_status.py \
    ~/postgresql_details/oceanomics.cfg mitogenome_status_$(date +%Y%m%d).csv
```

---

## Database tables used

| Table | Description |
|-------|-------------|
| `ref_genomes_assembly_uploads` | NCBI BioProject and assembly accessions per reference genome OG |
| `ref_genomes_sra_uploads` | SRR accessions and release status per sequencing run |
| `ref_genomes` | Assembly quality metrics and stage |
| `draft_genomes` | Draft genome assembly metrics and NCBI accessions |
| `mitogenome_data` | Mitogenome assembly records and GenBank accessions |
| `lca_validation` | Validated species names per OG |
| `sample` | Sample metadata, workflow, and embargo status |
