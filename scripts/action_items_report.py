#!/usr/bin/env python3
"""Action items report — a to-do list of OGs and what's needed next.

Queries the DB directly (like combined_upload_status.py) and produces a
categorized action list instead of a status table: for draft/ref/mito, each
non-"uploaded" upload_status becomes a plain-language action, and any
non-empty data_quality_flag becomes its own action; plus the new ToLID/NCBI
taxon readiness funnel (TOLID_SQL in queries.py — see that file for the full
bucket logic, scoped to sample.hifi = 'Y' per OcOm_SOP_B231's real trigger
population). ToLID-funnel actions are grouped under "Reference genomes",
since ToLID requests are reference-genome-specific.

Dedup: TOLID_SQL's "needs species validation" bucket and DRAFT_SQL's "no
validated species name recorded" flag test the same underlying fact (no
lca_validation row at all, any tech). Where an OG already got that fact
reported via the draft row, the TOLID_SQL version is skipped so it isn't
double-reported — but TOLID_SQL's bucket is still needed on its own, since
most hifi='Y' OGs (roughly 7 in 10) have no draft_genomes record at all and
would otherwise never surface this fact anywhere.

Produces reports/og_status_action_items.txt: grouped as
    <GENOME TYPE>
      <action category> (N OGs)
        og_id   species
        ...
one block per (genome type, action category) combination, so it reads as a
to-do list rather than a spreadsheet — an OG appears in every block it has
an outstanding action for. Also prints the same structure (counts only, no
OG listing) to stdout.

Only OGs with embargo_status = 'Release' are included — embargoed OGs
aren't being actively pushed out this quarter, so there's no action to take
on them yet. Blocks are ordered Reference genomes -> Mitogenomes -> Draft
genomes, matching the PDF summary's "List of uploaded OGs" order.

Usage:
    singularity run $SING/psycopg2:0.1.sif python3 action_items_report.py \
        ~/postgresql_details/oceanomics.cfg [output.txt]
"""
import configparser
import psycopg2
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import CLASSIFY, DRAFT_SQL, MITO_SQL, REF_SQL, TOLID_SQL, TYPES

IN_PROGRESS_LABEL = {
    'draft': 'needs raw/assembly upload to NCBI',
    'ref': 'needs upload to NCBI',
    # mito has no single label here -- its upload_status values ("ready to
    # submit", "needs_uploading", "submitted, pending accession", "submission
    # failed") are distinct, actionable states in their own right, so
    # build_actions() below uses the raw value as the category instead.
}

TOLID_GENOME_TYPE = 'Reference genomes'

# Reference -> Mitogenomes -> Draft, matching the PDF summary's appendix order.
BLOCK_ORDER = ['Reference genomes', 'Mitogenomes', 'Draft genomes']


def fetch(cur, sql):
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def build_actions(per_type_rows, tolid_rows):
    """Returns list of (genome_type, action_label, og_id, species) tuples."""
    actions = []
    no_validation_ogs = set()

    for t, label in TYPES:
        for row in per_type_rows[t]:
            if row.get('embargo_status') != 'Release':
                continue
            og_id = row['og_id']
            species = row.get('validated_species_name') or row.get('nominal_species_id') or '-'
            status = row.get('upload_status')
            bucket = CLASSIFY[t](status)
            if bucket == 'in_progress':
                action_label = status if t == 'mito' else IN_PROGRESS_LABEL[t]
                actions.append((label, action_label, og_id, species))

            flag = row.get('data_quality_flag')
            if flag:
                for part in flag.split(', '):
                    actions.append((label, part, og_id, species))
                    if t == 'draft' and part == 'no validated species name recorded':
                        no_validation_ogs.add(og_id)

    for row in tolid_rows:
        if row.get('embargo_status') != 'Release':
            continue
        action = row.get('action_needed')
        if not action:
            continue
        og_id = row['og_id']
        if action == 'needs species validation' and og_id in no_validation_ogs:
            continue  # already reported via draft's data_quality_flag
        species = row.get('validated_species_name') or row.get('nominal_species_id') or '-'
        actions.append((TOLID_GENOME_TYPE, action, og_id, species))

    return actions


def group_actions(actions):
    """{genome_type: {action_label: [(og_id, species), ...]}}, each list sorted by og_id."""
    grouped = defaultdict(lambda: defaultdict(list))
    for genome_type, action_label, og_id, species in actions:
        grouped[genome_type][action_label].append((og_id, species))
    for by_action in grouped.values():
        for entries in by_action.values():
            entries.sort(key=lambda e: e[0])
    return grouped


def render_text(grouped, generated_at):
    lines = [
        'OceanGenomes action items',
        f'Generated {generated_at}',
        '',
    ]
    for genome_type in BLOCK_ORDER:
        by_action = grouped.get(genome_type)
        if not by_action:
            continue
        header = genome_type.upper()
        lines.append('=' * len(header))
        lines.append(header)
        lines.append('=' * len(header))
        lines.append('')
        for action_label in sorted(by_action):
            entries = by_action[action_label]
            lines.append(f'{action_label} ({len(entries)} OGs)')
            lines.append('-' * len(f'{action_label} ({len(entries)} OGs)'))
            for og_id, species in entries:
                lines.append(f'{og_id:<14}{species}')
            lines.append('')
    return '\n'.join(lines)


def print_summary(grouped):
    print("\nAction items summary:")
    for genome_type in BLOCK_ORDER:
        by_action = grouped.get(genome_type)
        if not by_action:
            continue
        print(f"\n  {genome_type}:")
        for action_label in sorted(by_action):
            print(f"    {action_label}: {len(by_action[action_label])}")


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: action_items_report.py <config> [output.txt]')

    cfg = configparser.ConfigParser()
    cfg.read(sys.argv[1])
    p = cfg['postgres']
    conn = psycopg2.connect(dbname=p['dbname'], user=p['user'], password=p['password'],
                             host=p['host'], port=int(p['port']))
    cur = conn.cursor()

    per_type_rows = {
        'draft': fetch(cur, DRAFT_SQL),
        'ref': fetch(cur, REF_SQL),
        'mito': fetch(cur, MITO_SQL),
    }
    tolid_rows = fetch(cur, TOLID_SQL)
    conn.close()

    actions = build_actions(per_type_rows, tolid_rows)
    grouped = group_actions(actions)

    out_path = (Path(sys.argv[2]) if len(sys.argv) > 2
                else Path(__file__).resolve().parent.parent / 'reports' / 'og_status_action_items.txt')
    out_path.parent.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().astimezone().isoformat(timespec='seconds')
    out_path.write_text(render_text(grouped, generated_at))

    distinct_ogs = len(set(a[2] for a in actions))
    print(f"Written to {out_path} ({len(actions)} action entries across {distinct_ogs} OGs)")

    print_summary(grouped)


if __name__ == '__main__':
    main()
