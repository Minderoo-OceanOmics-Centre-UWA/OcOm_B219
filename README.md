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

- Without `output.csv`: prints tab-separated results to stdout with a summary.
- With `output.csv`: writes a CSV file and prints the summary.

---

## Scripts

### `ref_genome_upload_status.py`

Reports NCBI upload status for reference genomes at assembly stage 3.

**Filters:** OGs with a validated species name in `lca_validation` (deduplicated to the most recent record per OG) and a stage 3 entry in `ref_genomes`.

**Key columns:**

| Column | Source | Description |
|--------|--------|-------------|
| `og_id` | lca_validation | OG identifier |
| `tech` | lca_validation | Sequencing technology of the validated record |
| `validated_species_name` | lca_validation | Most recent validated species name |
| `nominal_species_id` / `common_name` / `tol_id` / `ncbi_id` / `workflow` | sample | Species and sample metadata |
| `biosample` | ref_genomes_assembly_uploads | NCBI BioSample accession (SAMN…) |
| `bioproject_umbrella` | ref_genomes_assembly_uploads | Species-level parent BioProject (PRJNA…) |
| `bioproject_hap1` | ref_genomes_assembly_uploads | Haplotype 1 assembly BioProject |
| `assembly_accession_hap1` | ref_genomes_assembly_uploads | GenBank assembly accession (JBXXXX…) |
| `bioproject_hap2` | ref_genomes_assembly_uploads | Haplotype 2 assembly BioProject |
| `assembly_accession_hap2` | ref_genomes_assembly_uploads | GenBank assembly accession (JBXXXX…) |
| `bioproject_rawdata` | ref_genomes_assembly_uploads | Raw sequencing data BioProject |
| `hifi_srr` / `hifi_status` | ref_genomes_sra_uploads | HiFi SRR accession and release status |
| `hic_srr` / `hic_status` | ref_genomes_sra_uploads | Hi-C SRR accession and release status |
| `ont_srr` / `ont_status` | ref_genomes_sra_uploads | ONT SRR accession and release status |
| `illumina_srr` / `illumina_status` | ref_genomes_sra_uploads | Illumina SRR accession and release status |
| `embargo_status` | ref_genomes_assembly_uploads, falls back to sample | Release or Embargo |
| `upload_status` | computed | See below |

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

---

### `draft_genome_upload_status.py`

Reports NCBI upload status for draft genomes, one row per `og_id` (using the latest `seq_date` for OGs with more than one `draft_genomes` record).

**Filters:** none — every `og_id` in `draft_genomes` that also has a matching `sample` row is included.

**Key columns:**

| Column | Source | Description |
|--------|--------|-------------|
| `og_id` | draft_genomes | OG identifier |
| `nominal_species_id` | sample | Species name |
| `aws_assm` | draft_genomes | S3 path to the assembly `.fna` file |
| `biosample_accession` | draft_genomes | NCBI BioSample accession (SAMN…) |
| `bioproject_accession` | draft_genomes | NCBI BioProject accession (PRJNA…) |
| `sra_accession` | draft_genomes | NCBI SRA accession (SRR…) |
| `assembly_accession` | draft_genomes | NCBI GenBank assembly accession (JBXXXX…) |
| `embargo_status` | sample | Release or Embargo |
| `upload_status` | computed | See below |

**Upload status values** (checked top-down, first match wins; the first three all require `aws_assm` to be present):

| Value | Meaning |
|-------|---------|
| `all uploaded` | `biosample_accession`, `bioproject_accession`, `sra_accession`, AND `assembly_accession` all present and validly formatted |
| `raw uploaded, assembly pending` | `biosample_accession`, `bioproject_accession`, AND `sra_accession` present, but `assembly_accession` still NULL |
| `raw+assembly needs uploading` | Assembly file ready (`aws_assm` present) but `biosample_accession`, `bioproject_accession`, `sra_accession`, AND `assembly_accession` are all NULL — nothing registered yet |
| `missing .fna or fields` | Catch-all: `aws_assm` is NULL (no assembly file yet), or any other partial state not covered above |

---

### `mitogenome_upload_status.py`

Reports GenBank upload status for mitogenomes, one row per OG using the best available sequencing technology (priority: hifi > hic > ilmn).

**Filters:**
- Excludes `_concat` OG IDs.
- Deduplicates to the most recent record per OG + technology.
- Selects one technology per OG by priority.

**Key columns:**

| Column | Source | Description |
|--------|--------|-------------|
| `og_id` | mitogenome_data | OG identifier |
| `og_num` | mitogenome_data | Numeric OG number |
| `tech` | mitogenome_data | Sequencing technology used (hifi/hic/ilmn) |
| `seq_date` | mitogenome_data | Sequencing date (YYMMDD) |
| `code` | mitogenome_data | Assembly pipeline version/code |
| `genbank_accession` | mitogenome_data | GenBank accession if submitted |
| `embargo_status` | sample | Release or Embargo |
| `upload_status` | computed | See below |

**Upload status values:**

| Value | Meaning |
|-------|---------|
| `hifi uploaded` | GenBank accession present, assembled from HiFi |
| `hic uploaded` | GenBank accession present, assembled from Hi-C |
| `ilmn uploaded` | GenBank accession present, assembled from Illumina |
| `needs_uploading` | No GenBank accession recorded |

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
