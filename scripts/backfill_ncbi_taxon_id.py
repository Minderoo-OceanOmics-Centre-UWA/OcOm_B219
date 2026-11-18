#!/usr/bin/env python3
"""Backfill sample.ncbi_id from master_species.ncbi_taxon_id.

Only updates where sample.ncbi_id is blank or non-numeric, the OG has a validated
species name signed off by both validators, and master_species has a valid integer
ncbi_taxon_id for that name. The taxon id is matched to the validated name, not
nominal_species_id, because the validated name is what the ENA pipelines submit and
ENA keys the organism off the taxon id. Requiring both validators keeps a one-person
re-identification from writing a taxon id for a different species.

Usage:
    singularity run $SING/psycopg2:0.1.sif python backfill_ncbi_taxon_id.py \
        ~/postgresql_details/oceanomics.cfg [--dry-run]
"""
import configparser
import psycopg2
import sys

DRY_RUN = '--dry-run' in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith('--')]
if not args:
    sys.exit('Usage: backfill_ncbi_taxon_id.py <config> [--dry-run]')

cfg = configparser.ConfigParser()
cfg.read(args[0])
p = cfg['postgres']
conn = psycopg2.connect(
    dbname=p['dbname'], user=p['user'], password=p['password'],
    host=p['host'], port=int(p['port'])
)
conn.autocommit = False

FIND_SQL = """
WITH validated AS (
    SELECT DISTINCT ON (og_id) og_id, validated_species_name
    FROM lca_validation
    WHERE validated_species_name IS NOT NULL AND validated_species_name <> ''
      AND NULLIF(BTRIM(validator::text), '')   IS NOT NULL
      AND NULLIF(BTRIM(validator_2::text), '') IS NOT NULL
    ORDER BY og_id, row_created_on DESC
)
SELECT
    s.og_id,
    v.validated_species_name,
    s.ncbi_id        AS current_ncbi_id,
    ms.ncbi_taxon_id AS new_ncbi_id
FROM sample s
JOIN validated v ON v.og_id = s.og_id
JOIN master_species ms ON ms.species = v.validated_species_name
WHERE (
    NULLIF(s.ncbi_id::text, '') IS NULL
    OR s.ncbi_id::text !~ '^[0-9]+(\\.0)?$'
)
  AND ms.ncbi_taxon_id IS NOT NULL
ORDER BY s.og_id;
"""

UPDATE_SQL = "UPDATE sample SET ncbi_id = %s WHERE og_id = %s"

cur = conn.cursor()
cur.execute(FIND_SQL)
rows = cur.fetchall()

if not rows:
    print("No samples need backfilling.")
    sys.exit(0)

updated = []
skipped = []

for og_id, validated_species_name, current_ncbi_id, ncbi_taxon_id in rows:
    raw = str(ncbi_taxon_id).strip() if ncbi_taxon_id else ''
    if raw.endswith('.0'):  # not rstrip('.0'), which would turn 372800 into 3728
        raw = raw[:-2]
    if not raw.isdigit():
        skipped.append((og_id, validated_species_name, ncbi_taxon_id, 'not numeric'))
        continue
    updated.append((og_id, validated_species_name, current_ncbi_id, int(raw)))

print(f"{'DRY RUN — ' if DRY_RUN else ''}Found {len(rows)} sample(s) with missing/invalid ncbi_id and a master_species match on a fully validated name.\n")

for og_id, species, current, taxon_id in updated:
    current_str = repr(current) if current else 'NULL'
    print(f"  {'[would update]' if DRY_RUN else '[updating]'} {og_id}  {species}  {current_str} → {taxon_id}")

if skipped:
    print(f"\nSkipped {len(skipped)} (master_species.ncbi_taxon_id not numeric):")
    for og_id, species, raw, reason in skipped:
        print(f"  {og_id}  {species}  ncbi_taxon_id={raw!r}  ({reason})")

if not DRY_RUN and updated:
    for og_id, _, _current, taxon_id in updated:
        cur.execute(UPDATE_SQL, (str(taxon_id), og_id))
    conn.commit()
    print(f"\n✅ Updated {len(updated)} sample(s).")
elif DRY_RUN:
    print(f"\n(Dry run — no changes written. Re-run without --dry-run to apply.)")

conn.close()
