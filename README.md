# OG Upload Status Quaterly Report

Scripts for reporting data sharing status across Ocean Genomes: reference genomes, draft genomes, and mitogenomes.

## Layout

- `run_combined_report.sh` — one-command entry point for the quarterly review (see [Combined report](#combined-report)).
- `scripts/` — all source: the three per-type scripts, `queries.py`, `combined_upload_status.py`, `generate_html_report.py`, and `report_template.html`.
- `reports/` — generated output only (CSVs, the HTML report); `reports/archive/` accumulates a dated copy of every combined-report run for quarterly comparison.

---

## Requirements

- Singularity container: `$SING/psycopg2:0.1.sif`
- PostgreSQL credentials: `~/postgresql_details/oceanomics.cfg`

---

## Usage

```bash
bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg
```

Writes `og_status.csv`, `og_status_flagged.csv`, and `og_status_report.html` to `reports/`. A dated copy of both CSVs is also saved to `reports/archive/` on every run (e.g. `og_status_20260819_160501.csv`).Open the HTML file in a browser to view the report.

---

## Individual per-type scripts

Use these instead of the combined report when you need full per-type detail (e.g. ref genome haplotype/SRA columns, draft accession columns) — the combined view only carries the normalized status/flag columns.

```bash
singularity run $SING/psycopg2:0.1.sif python scripts/<script.py> \
    ~/postgresql_details/oceanomics.cfg
```

Writes `<type>_status_<YYYYMMDD_HHMMSS>.csv` (the full report) and `<type>_status_<YYYYMMDD_HHMMSS>_data_quality_issues.csv` (same rows, filtered to a non-empty `data_quality_flag`) to `reports/`.

| Script | Reports on |
|---|---|
| `ref_genome_upload_status.py` | Reference genomes upload status |
| `draft_genome_upload_status.py` | Draft genomes upload status |
| `mitogenome_upload_status.py` | Mitogenomes upload status |

Each script's own docstring documents its exact `upload_status` and `data_quality_flag` values; the full SQL (with inline comments) lives in `scripts/queries.py`.

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
