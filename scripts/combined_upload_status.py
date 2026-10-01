#!/usr/bin/env python3
"""Combined upload status report - draft + reference + mitogenomes in one view.

Runs all three queries and pivots to one row per og_id, carrying both publication
channels side by side: accessions at ENA/NCBI, and what is public on the open-data
bucket (see public_release.py).

Usage:
    singularity run $SING/psycopg2:0.1.sif python combined_upload_status.py \
        ~/postgresql_details/oceanomics.cfg [output.csv] [--listing listing.txt]
"""
import configparser, psycopg2, csv, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import public_release
from queries import DRAFT_SQL, REF_SQL, MITO_SQL

TYPES = ('draft', 'ref', 'mito')

EXTRA_COLS = {
    'draft': {
        'draft_assembly_published': 'assembly_published',
        'draft_raw_submitted': 'raw_submitted',
    },
    'ref': {
        'ref_assembly_published': 'assembly_published',
        'ref_raw_submitted': 'raw_submitted',
        'ref_raw_hifi': 'hifi_release',
        'ref_raw_hic': 'hic_release',
        'ref_raw_ont': 'ont_release',
        'ref_raw_illumina': 'illumina_release',
        'ref_raw_unknown': 'unknown_release',
    },
    'mito': {
        'mito_assembly_published': 'assembly_published',
        'mito_cohort': 'cohort',
    },
}

S3_COLS = ['s3_assemblies', 's3_mito', 's3_raw_hifi', 's3_raw_hic']

COMBINED_COLS = (
    ['og_id', 'species', 'tol_id', 'embargo_status']
    + [c for t in TYPES for c in ([f'{t}_upload_status', f'{t}_data_quality_flag'] + list(EXTRA_COLS[t]))]
    + S3_COLS
    + ['generated_at']
)


def fetch_type(cur, sql):
    """Run one type's query, return {og_id: {column: value}}."""
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return {r[cols.index('og_id')]: dict(zip(cols, r)) for r in cur.fetchall()}


def default_listing():
    return Path(__file__).resolve().parent.parent / 'reports' / 'public_release_listing.txt'


def main():
    args = [a for a in sys.argv[1:]]
    listing_path = default_listing()
    if '--listing' in args:
        i = args.index('--listing')
        listing_path = Path(args[i + 1])
        del args[i:i + 2]

    cfg = configparser.ConfigParser()
    cfg.read(args[0])
    p = cfg["postgres"]
    conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                            host=p["host"], port=int(p["port"]))
    cur = conn.cursor()

    per_type = {
        'draft': fetch_type(cur, DRAFT_SQL),
        'ref':   fetch_type(cur, REF_SQL),
        'mito':  fetch_type(cur, MITO_SQL),
    }
    conn.close()

    if listing_path.exists():
        entries = public_release.load(listing_path)
        print(f"Open-data listing: {len(entries)} published (species, ToLID) pair(s) from {listing_path}")
    else:
        entries = None
        print(f"No open-data listing at {listing_path} — S3 columns left blank. "
              f"Run scripts/public_release.py to fetch one.")

    all_og_ids = sorted(set().union(*(d.keys() for d in per_type.values())))
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')

    combined = []
    for og_id in all_og_ids:
        species = tol_id = embargo_status = None
        for t in TYPES:
            rec = per_type[t].get(og_id)
            if rec:
                species = species or rec.get('validated_species_name')
                tol_id = tol_id or rec.get('tol_id')
                embargo_status = embargo_status or rec.get('embargo_status')

        row = [og_id, species, tol_id, embargo_status]
        for t in TYPES:
            rec = per_type[t].get(og_id) or {}
            row.append(rec.get('upload_status'))
            row.append(rec.get('data_quality_flag'))
            for src in EXTRA_COLS[t].values():
                row.append(rec.get(src))

        flags = public_release.lookup(entries, species, tol_id) if entries else None
        row += [flags['assemblies'], flags['mito'], flags['raw_hifi'], flags['raw_hic']] if flags \
            else [None] * len(S3_COLS)

        row.append(generated_at)
        combined.append(row)

    out_path = Path(args[1]) if len(args) > 1 else None
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
        status_idx = COMBINED_COLS.index(f'{t}_upload_status')
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

    for t, idx in zip(TYPES, flag_idxs):
        n = sum(1 for row in flagged_rows if row[idx])
        print(f"  {t}: {n}")


if __name__ == '__main__':
    main()
