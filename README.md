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

**Filters:** OGs with a validated species name in `lca_validation` and a stage 3 entry in `ref_genomes`.

**Key columns:**

| Column | Source | Description |
|--------|--------|-------------|
| `og_id` | lca_validation | OG identifier |
| `validated_species_name` | lca_validation | Most recent validated species name |
| `nominal_species_id` | sample | Nominal species |
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
| `embargo_status` | ref_genomes_assembly_uploads | Release or Embargo |
| `upload_status` | computed | See below |

**Upload status values:**

| Value | Meaning |
|-------|---------|
| `all uploaded` | Both haplotype assembly accessions (JBXXXX) present and at least one SRR released |
| `hap1 uploaded` | Hap1 GenBank assembly accession assigned |
| `hap1 pending` | Hap1 BioProject registered but assembly not yet submitted |
| `hap2 uploaded` | Hap2 GenBank assembly accession assigned |
| `hap2 pending` | Hap2 BioProject registered but assembly not yet submitted |
| `raw data released` | At least one SRR accession in Released state |
| `raw data pending` | Rawdata BioProject registered but no SRR released |
| `not registered on NCBI` | No entry in `ref_genomes_assembly_uploads` |

---

### `draft_genome_upload_status.py`

Reports upload status for draft genomes that need submitting to NCBI.

**Filters:** OGs where either:
- `sample.workflow = 'Draft'` (or unassigned + Illumina sequenced) and no NCBI accessions exist in `sample`; or
- A `draft_genomes` record exists without valid BioSample, BioProject, or SRR accessions.

**Key columns:**

| Column | Source | Description |
|--------|--------|-------------|
| `og_id` | sample | OG identifier |
| `nominal_species_id` | sample | Species name |
| `aws_assm` | draft_genomes | S3 path to the assembly `.fna` file |
| `biosample_accession` | draft_genomes | NCBI BioSample accession (SAMN…) |
| `bioproject_accession` | draft_genomes | NCBI BioProject accession (PRJNA…) |
| `sra_accession` | draft_genomes | NCBI SRA accession (SRR…) |
| `draft_sra_accessions` | sample | SRR accessions recorded in sample table |
| `embargo_status` | sample | Release or Embargo |
| `upload_status` | computed | See below |

**Upload status values:**

| Value | Meaning |
|-------|---------|
| `uploaded` | Valid BioSample, BioProject, and SRR accessions all present |
| `needs_uploading` | Assembly `.fna` is on S3 but accessions are missing |
| `missing .fna` | No assembly file found on S3 yet |

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
