#!/usr/bin/env python3
"""Combined NCBI upload status report — draft + reference + mitogenomes in one view.

Runs the same queries as draft_genome_upload_status.py, ref_genome_upload_status.py,
and mitogenome_upload_status.py (imported from queries.py, so there's one source of
truth for the logic), then pivots to ONE ROW PER og_id — since an OG can have a
draft, ref, and/or mito record at once, each type gets its own upload_status /
data_quality_flag columns side by side rather than being listed as separate rows.

Produces:
  1. The pivoted combined table (see COMBINED_COLS below).
  2. A summary: total OGs and upload_status breakdown per type, plus a grand total.
  3. A combined data quality issues report (OGs with a flag in any type).

For full per-type detail (e.g. ref genome haplotype/SRA columns, draft accession
columns), run the individual scripts — this report is intentionally the normalized
"at a glance" view, not a replacement for them.

Usage:
    singularity run $SING/psycopg2:0.1.sif python combined_upload_status.py \
        ~/postgresql_details/oceanomics.cfg [output.csv]
"""
import configparser, psycopg2, csv, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import DRAFT_SQL, REF_SQL, MITO_SQL

TYPES = ('draft', 'ref', 'mito')

COMBINED_COLS = [
    'og_id', 'species', 'embargo_status',
    'draft_upload_status', 'draft_data_quality_flag',
    'ref_upload_status', 'ref_data_quality_flag',
    'mito_upload_status', 'mito_data_quality_flag',
    'generated_at',
]


def fetch_type(cur, genome_type, sql, species_col):
    """Run one type's query, return {og_id: {species, embargo_status, upload_status, data_quality_flag}}."""
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    idx = {name: i for i, name in enumerate(cols)}
    by_og = {}
    for r in rows:
        og_id = r[idx['og_id']]
        by_og[og_id] = {
            'species': r[idx[species_col]],
            'embargo_status': r[idx['embargo_status']],
            'upload_status': r[idx['upload_status']],
            'data_quality_flag': r[idx['data_quality_flag']],
        }
    return by_og


def main():
    cfg = configparser.ConfigParser()
    cfg.read(sys.argv[1])
    p = cfg["postgres"]
    conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                            host=p["host"], port=int(p["port"]))
    cur = conn.cursor()

    per_type = {
        'draft': fetch_type(cur, 'draft', DRAFT_SQL, 'validated_species_name'),
        'ref':   fetch_type(cur, 'ref',   REF_SQL,   'validated_species_name'),
        'mito':  fetch_type(cur, 'mito',  MITO_SQL,  'validated_species_name'),
    }
    conn.close()

    all_og_ids = sorted(set().union(*(d.keys() for d in per_type.values())))

    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')

    combined = []
    for og_id in all_og_ids:
        species = embargo_status = None
        for t in TYPES:
            rec = per_type[t].get(og_id)
            if rec:
                species = species or rec['species']
                embargo_status = embargo_status or rec['embargo_status']

        row = [og_id, species, embargo_status]
        for t in TYPES:
            rec = per_type[t].get(og_id)
            row.append(rec['upload_status'] if rec else None)
            row.append(rec['data_quality_flag'] if rec else None)
        row.append(generated_at)
        combined.append(row)

    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if out_path:
        with open(out_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(COMBINED_COLS)
            writer.writerows(combined)
        print(f"Written to {out_path}")
    else:
        print('\t'.join(COMBINED_COLS))
        for row in combined:
            print('\t'.join('' if x is None else str(x) for x in row))

    print(f"\nTotal: {len(combined)} distinct OGs across draft/ref/mito")
    for t in TYPES:
        status_col = f'{t}_upload_status'
        status_idx = COMBINED_COLS.index(status_col)
        type_statuses = [row[status_idx] for row in combined if row[status_idx] is not None]
        print(f"\n  {t} ({len(per_type[t])} OGs):")
        for status, count in sorted(Counter(type_statuses).items()):
            print(f"    {status}: {count}")

    flag_idxs = [COMBINED_COLS.index(f'{t}_data_quality_flag') for t in TYPES]
    flagged_rows = [row for row in combined if any(row[i] for i in flag_idxs)]

    if out_path:
        flag_path = out_path.with_name(f"{out_path.stem}_flagged{out_path.suffix}")
        with open(flag_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(COMBINED_COLS)
            writer.writerows(flagged_rows)
        print(f"\nData quality issues ({len(flagged_rows)} OGs) written to {flag_path}")
    else:
        print(f"\nData quality issues: {len(flagged_rows)} OGs")
        if flagged_rows:
            print('\t'.join(COMBINED_COLS))
            for row in flagged_rows:
                print('\t'.join('' if x is None else str(x) for x in row))

    for t, idx in zip(TYPES, flag_idxs):
        n = sum(1 for row in flagged_rows if row[idx])
        print(f"  {t}: {n}")


if __name__ == '__main__':
    main()
