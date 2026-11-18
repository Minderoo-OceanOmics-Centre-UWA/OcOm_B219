# OG Upload Status Quarterly Report

Reporting on data sharing across Ocean Genomes: reference genomes, draft genomes, and mitogenomes.

## Layout

- `run_combined_report.sh` — the entry point
- `scripts/`
- `reports/` — generated output only; `reports/archive/` keeps a dated copy of the summary PDF from every run, for quarterly comparison.

---

## Usage

Needs the `$SING/psycopg2:0.1.sif` container, `~/postgresql_details/oceanomics.cfg`, `rclone` configured with the `opendata:` remote, and `fpdf2` + `pypdf` for the PDF. Those two are not in the Pawsey `python` module, so the PDF step
uses the venv at `/software/projects/pawsey1348/olivianguyen/tools/ocom_pdf`; set `PDF_PYTHON` to
override it.

```bash
# Triage during the quarter — CSVs + action items only:
bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg --check

# Generate and archive each quarter report:
bash run_combined_report.sh ~/postgresql_details/oceanomics.cfg
```

Both write to `reports/`:
- `og_status.csv` — full per-OG data. An intermediate rather than a deliverable: `generate_summary_report.py` reads it to build the PDF.
- `og_status_action_items.txt` — a to-do list grouped by genome type then action, with the OGs needing each action listed underneath. Every assembled, unsubmitted OG appears **exactly once per genome type**, so each action is a work queue and the category counts sum to the section total. Each section also reports how many OGs are held back by embargo rather than by anything you can act on.

`--check` is safe to re-run as often as you like: it never touches `reports/archive/` and reuses the cached bucket listing rather than re-fetching it. It is **not** read-only — like the full run, it first backfills any newly-issued GCA accessions (`resolve_gca_accessions.py`), so the report never reports an assembly as unpublished when ENA has already published it. That write is blank-only: it only fills a missing accession and never overwrites or clears one, so a differing value is reported as a conflict instead.

The run without `--check` is the single official quarterly run. It additionally:
1. refreshes the open-data bucket listing,
2. builds `og_status_summary.pdf` and archives a dated copy.

---

## Where the data comes from

Four pipelines feed the columns this report reads. None of them is run from here.

| Stage | Pipeline | What it records |
|---|---|---|
| Compile + publish reference genomes | `refgen-compile` | Nothing in the database. Publishes to the open-data S3 bucket; the bucket listing is the only record that it happened. |
| Reference genome submission | `ENA-ref-genomes`, steps `00`–`09` | Step `09` writes `sample`, `ref_genomes_assembly_uploads`, `ref_genomes_sra_uploads`. |
| Draft genome submission | `ENA-draft-genomes`, steps `00`–`06` | Step `06` writes `sample.ncbi_biosample_id` and `draft_genomes`. |
| Mitogenome submission | `ENA-mito-genomes`, steps `00`–`06` | Step `06` writes `ena_submissions` and `sample.ncbi_biosample_id`. |
| Public GCA accessions | **here**, `scripts/resolve_gca_accessions.py` | Fills all three types in one quarterly pass, as the first step of `run_combined_report.sh`, so the report reads current data. Each pipeline also ships its own resolver (ref `10`, draft `07`, mito `07`); running either is fine, the writes are blank-only. |

### Two publication channels

- **ENA/NCBI** — accessions, for all three genome types.
- **Open-data S3 bucket** (`opendata:minderoo-oceanomics/species/`) — reference genomes and the mitogenomes published alongside them. Draft genomes have no open-data channel.

---

## Scripts

`run_combined_report.sh` runs these in order, but any of them can be run on its own:

```bash
singularity run $SING/psycopg2:0.1.sif python scripts/<script.py> \
    ~/postgresql_details/oceanomics.cfg
```

| Script | Purpose |
|---|---|
| `combined_upload_status.py` | All three genome types pivoted to one row per OG |
| `action_items_report.py` | The quarterly to-do list, grouped by action |
| `generate_summary_report.py` | The manager-facing PDF (reads the combined CSV, no DB access) |
| `resolve_gca_accessions.py` | Fills public GCA accessions for all three types. `--dry-run` to preview. Kept self-contained so this repo runs without the pipelines checked out; its matching and write logic duplicates theirs, so **edit it together with** `ENA-ref-genomes/workflow/10_*`, `ENA-draft-genomes/workflow/07_*` and `ENA-mito-genomes/workflow/07_*` |
| `public_release.py` | Refreshes and reads the open-data bucket listing |
| `backfill_ncbi_taxon_id.py` | Fills `sample.ncbi_id` from `master_species`. `--dry-run` to preview |

---

## What each OG is waiting on

`blocking_action` in `scripts/queries.py` gives each assembled, unsubmitted OG one action:
the first gate below that it fails. First match wins, evaluated on the row the report picks
for that OG (see the next section).

| # | Action | Condition |
|---|---|---|
| 1 | `species name not validated` | unless `lca_validation` has a `validated_species_name` **and** both `validator` and `validator_2` are filled, it lands here |
| 2 | `validation not for this assembly` | the validated name comes from a different `seq_date` or tech than the row being reported, so it may not describe what is being uploaded |
| 3 | `species mismatch: validated vs nominal` | the validated name differs from `sample.nominal_species_id` |
| 4 | `hap1/hap2 seq_date inconsistent` | reference genomes only — the two stage-3 haplotypes carry different `seq_date` values, so what is being submitted is ambiguous |
| 5 | `flatfile not validated for ENA` | mitogenomes only — `ena_validation_latest.submission_ready` is false |
| 6 | `missing taxon ID` | `sample.ncbi_id` is blank or not numeric |
| 7 | `missing ToLID` | reference genomes only — `sample.tol_id` is blank or is not a ToLID. 3 rows hold a status note such as `Requested 11/06/2024`. Draft and mitogenome submissions do not need one |
| — | `ready to submit` | nothing above matched |

Validation comes first because the rest is wasted effort on an OG whose species is not
settled, and the two validator columns are both required: `mito_lca_validation` records
that "submission needs both". Blank and malformed values share an action because they are
the same job. The report is deliberately stricter than `ENA-ref-genomes` and
`ENA-mito-genomes`, which do not check either validator; `ENA-draft-genomes` does, and
already excludes any row carrying a data quality flag.

`blocking_action` replaced the older `data_quality_flag`: every value that flag carried is
now either one of the gates above or a duplicate of one, so the two have been merged.

---

## Which row each OG is reported on

Every OG has several candidate rows (sequencing runs, techs, assembly codes, haplotypes), but the
report shows one status per OG per genome type. The pick **prefers a row that has a submission**,
and only falls back to recency when no row does. Without that preference the report claimed an OG
was unsubmitted whenever its published assembly sat on a row the pick passed over — it was
undercounting 9 OGs (7 mito, 1 draft, 1 ref) as of 2026-11-12.

| Type | Pick order |
|------|-----------|
| mito | tech with a production `ena_submissions` row first, else `hifi` > `hic` > `ilmn`; within a tech, the submitted row first, then newest `seq_date`, then `code` |
| draft | row with any accession first, then one with an `ilmn` validation matching `seq_date`, then newest `seq_date` (parsed as `YYMMDD`; non-conforming values rank last) |
| ref | stage-3 OGs **plus** any OG with a row in `ref_genomes_assembly_uploads`, so a published genome still counts if its recorded stage is not 3 |

`draft_genome.seq_date` is text and not always a date — `230419-genomescope` exists and used to
outrank the real `230419` row under a plain string sort. The draft pick parses it instead, but the
2 affected rows are worth correcting at source.

---

## Database relations used

All reporting reads from views. Only `resolve_gca_accessions.py` and `backfill_ncbi_taxon_id.py`
write, and they write to base tables — the views below are not updatable, so an `UPDATE` against
one fails with `cannot update view`.

| Relation | Kind | Description |
|----------|------|-------------|
| `ref_genomes_assembly_uploads` / `ref_genomes_sra_uploads` / `ref_genomes` | view | Reference genome accessions, raw-data release state, and stage |
| `draft_genomes` | view | Draft genome assembly metrics and accessions (`ena_analysis_accession` is the ERZ, `assembly_accession` the public GCA) |
| `mitogenome_data` | view | Mitogenome assembly records (identity/QC fields only — `genbank_accession` no longer exists; ENA accessions come from `ena_submissions`) |
| `ena_validation_latest` | view | Flatfile format validation per mitogenome assembly (drives `ready to submit`) |
| `lca_validation` | view | Validated species names per OG; `validator` and `validator_2` drive readiness, and both are required |
| `ena_submissions` | table | ENA submission ledger for mitogenomes (drives `submitted, ...` statuses); mito GCA is written here |
| `sample` | table | Sample metadata, ToLID, workflow, and embargo status; `ncbi_id` is written here |
| `master_species` | table | NCBI taxon IDs (used only by `backfill_ncbi_taxon_id.py`) |
| `ref_submission` / `ref_submission_haplotype` | table | Base tables behind `ref_genomes_assembly_uploads`; ref GCA is written to `ref_submission_haplotype.gca_accession`, keyed by `haplotype` |
| `draft_genome` / `draft_submission` | table | Base tables behind `draft_genomes`; draft GCA is written to `draft_submission.assembly_accession`, with `draft_id` resolved from `draft_genome (og_id, seq_date)` |

---

## Each quarter

1. `--check` during the quarter; work through `og_status_action_items.txt`. Each run also backfills any GCA accessions ENA has issued since the last one, so running it often keeps the database current.
2. When the submissions for the quarter are done, run without `--check`.
3. View summary report under `reports/og_status_summary.pdf` — built by the full run only, so it is absent after a `--check` run.
4. Commit and push the archived PDF — next quarter's report reads its embedded counts to compute deltas, and scratch is not durable storage.

```bash
git add reports/archive/<new PDF>
git commit -m "Archive quarterly summary PDF"
git push
```
