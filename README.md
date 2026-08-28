# OG Upload Status Quaterly Report

Scripts for reporting data sharing status across Ocean Genomes: reference genomes, draft genomes, and mitogenomes.

## Layout

- `run_combined_report.sh`
- `scripts/`
- `reports/` — generated output only; `reports/archive/` keeps a dated copy of the summary PDF from every run, for quarterly comparison.

---

## Requirements

- Singularity container: `$SING/psycopg2:0.1.sif`
- PostgreSQL credentials: `~/postgresql_details/oceanomics.cfg`

---

## Usage

```bash
# Triage during the quarter — CSVs + action items only:
bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg --check

# Generate and archive each quarter report:
bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg
```

Both write to `reports/`:
- `og_status.csv` / `og_status_flagged.csv` — full per-OG data.
- `og_status_action_items.txt` — a to-do list grouped by genome type then action category (e.g. "needs NCBI taxon ID", "needs ToLID request"), with the OGs needing each action listed underneath. Full category list: each `data_quality_flag` in `scripts/queries.py`.

`--check` is safe to re-run as often as you like — it never touches `reports/archive/`.

---

## Individual per-type scripts

For full per-type detail the combined report normalizes away (e.g. ref haplotype/SRA columns):

```bash
singularity run $SING/psycopg2:0.1.sif python scripts/<script.py> \
    ~/postgresql_details/oceanomics.cfg
```

| Script | Reports on |
|---|---|
| `ref_genome_upload_status.py` | Reference genomes |
| `draft_genome_upload_status.py` | Draft genomes |
| `mitogenome_upload_status.py` | Mitogenomes |

Each writes a timestamped CSV + flagged-only CSV to `reports/`. Exact `upload_status`/`data_quality_flag` values are documented in each script's own docstring; full SQL lives in `scripts/queries.py`.

---

## Database tables used

| Table | Description |
|-------|-------------|
| `ref_genomes_assembly_uploads` / `ref_genomes_sra_uploads` / `ref_genomes` | Reference genome accessions, SRA status, and stage |
| `draft_genomes` | Draft genome assembly metrics and NCBI accessions |
| `mitogenome_data` | Mitogenome assembly records (identity/QC fields only — `genbank_accession` is dead, no longer written) |
| `ena_validation_attempts` | Flatfile format validation per mitogenome assembly (drives `ready to submit`) |
| `ena_submissions` | Actual ENA submission ledger for mitogenomes (drives `submitted, ...` statuses) |
| `lca_validation` | Validated species names per OG |
| `sample` | Sample metadata, workflow, and embargo status |
