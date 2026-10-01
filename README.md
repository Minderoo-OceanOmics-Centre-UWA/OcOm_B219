# OG Upload Status Quarterly Report

Reporting on data sharing across Ocean Genomes: reference genomes, draft genomes, and mitogenomes.

This repo reports; it does not submit. The submission pipelines write accessions back to the database, and this reads them to answer one question each quarter: **what have we shared, and what is actually public?**

## Layout

- `run_combined_report.sh` — the entry point
- `scripts/`
- `reports/` — generated output only; `reports/archive/` keeps a dated copy of the summary PDF from every run, for quarterly comparison.

---

## Requirements

- Singularity container: `$SING/psycopg2:0.1.sif`
- PostgreSQL credentials: `~/postgresql_details/oceanomics.cfg`
- `rclone`, configured with the `opendata:` remote — for the open-data bucket listing
- `fpdf2` and `pypdf` for the PDF (`pip3 install --user fpdf2 pypdf`)

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

`--check` is read-only and safe to re-run as often as you like: it writes nothing to the database, never touches `reports/archive/`, and reuses the cached bucket listing rather than re-fetching it.

The run without `--check` is the single official quarterly run. It additionally:
1. refreshes the open-data bucket listing,
2. fills in any newly-issued GCA accessions (`resolve_gca_accessions.py` — this writes to the database),
3. builds `og_status_summary.pdf` and archives a dated copy.

---

## Where the data comes from

Four pipelines feed the columns this report reads. None of them is run from here.

| Stage | Pipeline | What it records |
|---|---|---|
| Compile + publish reference genomes | `refgen-compile` | Nothing in the database. Publishes to the open-data S3 bucket; the bucket listing is the only record that it happened. |
| Reference genome submission | `ENA-ref-genomes`, steps `00`–`09` | Step `09` writes `sample`, `ref_genomes_assembly_uploads`, `ref_genomes_sra_uploads`. |
| Draft genome submission | `ENA-draft-genomes`, steps `00`–`06` | Step `06` writes `sample.ncbi_biosample_id` and `draft_genomes`. |
| Mitogenome submission | `ENA-mito-genomes`, steps `00`–`06` | Step `06` writes `ena_submissions` and `sample.ncbi_biosample_id`. |
| Public GCA accessions | **here**, `scripts/resolve_gca_accessions.py` | Fills all three types in one quarterly pass. Each pipeline also ships its own resolver (ref `10`, draft `07`, mito `07`); running either is fine, the writes are blank-only. |

A write-back step only runs under `--prod_mode true`, so a TEST submission never reaches these tables.

### Two publication channels

- **ENA/SRA** — accessions, for all three genome types.
- **Open-data S3 bucket** (`opendata:minderoo-oceanomics/species/`) — reference genomes and the mitogenomes published alongside them. Draft genomes have no open-data channel.

They move independently, so the report shows both. Raw reads embargoed at ENA while already public on the bucket is a real and expected state, not an error.

---

## Reading the statuses

**An accession existing means the data was submitted.** Release state is reported alongside it, never as a precondition — so an embargoed OG still counts as submitted.

`ref_genomes_sra_uploads.ncbi_status` carries three vocabularies at once, normalised by `RELEASE_STATE_SQL` in `scripts/queries.py`:

| Value | Meaning |
|---|---|
| `Released` | public |
| `ToBeReleased` | embargoed, pending |
| a date, e.g. `2028-06-29` | the holdUntilDate written by ENA step `09`; embargoed until then, public once it has passed |
| empty | unknown — reported as such, counted as neither |

The terminal status per type, and what "public" means for each:

| Type | Submitted | Public |
|---|---|---|
| Reference | `all uploaded` (both haplotype ERZs + raw data) | `all uploaded, public` (both haplotype GCAs) |
| Draft | `submitted, awaiting GCA` (ERZ in `ena_analysis_accession`) | `all uploaded` (GCA in `assembly_accession`) |
| Mitogenome | `submitted, accession assigned` | `submitted, public` (GCA in `ena_assembly_accession`) |

Both count as uploaded for the headline total, so the number stays comparable with previously archived reports. Exact per-type value lists live in each script's docstring; the SQL is in `scripts/queries.py`.

---

## Individual per-type scripts

For full per-type detail the combined report normalizes away (e.g. ref haplotype/SRA columns):

```bash
singularity run $SING/psycopg2:0.1.sif python scripts/<script.py> \
    ~/postgresql_details/oceanomics.cfg
```

| Script | Purpose |
|---|---|
| `ref_genome_upload_status.py` | Reference genomes |
| `draft_genome_upload_status.py` | Draft genomes |
| `mitogenome_upload_status.py` | Mitogenomes |
| `combined_upload_status.py` | All three pivoted to one row per OG |
| `action_items_report.py` | The quarterly to-do list, grouped by action |
| `generate_summary_report.py` | The manager-facing PDF (reads the combined CSV, no DB access) |
| `resolve_gca_accessions.py` | Fills public GCA accessions for all three types. `--dry-run` to preview |
| `public_release.py` | Refreshes and reads the open-data bucket listing |
| `backfill_ncbi_taxon_id.py` | Fills `sample.ncbi_id` from `master_species`. `--dry-run` to preview |

The three per-type scripts each write a timestamped CSV + flagged-only CSV to `reports/`.

---

## Database tables used

| Table | Description |
|-------|-------------|
| `ref_genomes_assembly_uploads` / `ref_genomes_sra_uploads` / `ref_genomes` | Reference genome accessions, raw-data release state, and stage |
| `draft_genomes` | Draft genome assembly metrics and accessions (`ena_analysis_accession` is the ERZ, `assembly_accession` the public GCA) |
| `mitogenome_data` | Mitogenome assembly records (identity/QC fields only — `genbank_accession` is dead, no longer written) |
| `ena_validation_latest` | Flatfile format validation per mitogenome assembly (drives `ready to submit`) |
| `ena_submissions` | ENA submission ledger for mitogenomes (drives `submitted, ...` statuses) |
| `lca_validation` | Validated species names per OG |
| `sample` | Sample metadata, ToLID, workflow, and embargo status |
| `master_species` | NCBI taxon IDs (used only by `backfill_ncbi_taxon_id.py`) |

---

## Each quarter

1. `--check` during the quarter; work through `og_status_action_items.txt`.
2. When the submissions for the quarter are done, run without `--check`.
3. Send `reports/og_status_summary.pdf` to the manager.
4. Commit and push the archived PDF — next quarter's report reads its embedded counts to compute deltas, and scratch is not durable storage.

```bash
git add reports/archive/<new PDF>
git commit -m "Archive quarterly summary PDF"
git push
```
