#!/usr/bin/env python3
"""Action items report - a to-do list of OGs and what is needed next.

Every assembled, unsubmitted OG gets exactly one action: the first unmet gate of the
readiness funnel in queries.py (blocking_action), or "ready to submit" when nothing
blocks it. One action per OG is what makes each category a work queue rather than an
overlapping flag list. Only OGs with embargo_status = 'Release' are included; the
number held back by embargo is reported per section.

Usage:
    singularity run $SING/psycopg2:0.1.sif python3 action_items_report.py \
        ~/postgresql_details/oceanomics.cfg [output.txt]

    Writes reports/og_status_action_items.txt, grouped by genome type then action.
"""
import configparser
import psycopg2
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import DRAFT_SQL, MITO_SQL, REF_SQL, TYPES, classify

READY = 'ready to submit'

ACTION_ORDER = [
    READY,
    'species name not validated',
    'validation not for this assembly',
    'hap1/hap2 seq_date inconsistent',
    'flatfile not validated for ENA',
    'missing taxon ID',
    'missing ToLID',
]

BLOCK_ORDER = ['Reference genomes', 'Mitogenomes', 'Draft genomes']


def fetch(cur, sql):
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def build_actions(per_type_rows):
    """Returns (actions, embargoed): one (genome_type, action, og_id, species) per OG,
    and {genome_type: count} held back by embargo."""
    actions = []
    embargoed = defaultdict(int)

    for t, label in TYPES:
        for row in per_type_rows[t]:
            if classify(t, row.get('upload_status')) != 'in_progress':
                continue
            if row.get('embargo_status') != 'Release':
                embargoed[label] += 1
                continue
            species = row.get('validated_species_name') or row.get('nominal_species_id') or '-'
            actions.append((label, row.get('blocking_action') or READY, row['og_id'], species))

    return actions, embargoed


def action_sort_key(action_label):
    if action_label in ACTION_ORDER:
        return (ACTION_ORDER.index(action_label), action_label)
    return (len(ACTION_ORDER), action_label)


def group_actions(actions):
    """{genome_type: {action_label: [(og_id, species), ...]}}, each list sorted by og_id."""
    grouped = defaultdict(lambda: defaultdict(list))
    for genome_type, action_label, og_id, species in actions:
        grouped[genome_type][action_label].append((og_id, species))
    for by_action in grouped.values():
        for entries in by_action.values():
            entries.sort(key=lambda e: e[0])
    return grouped


def render_text(grouped, embargoed, generated_at):
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
        held = embargoed.get(genome_type, 0)
        total = sum(len(e) for e in by_action.values())
        lines.append(f'{total} assembled and unsubmitted; {held} more held back by embargo')
        lines.append('')
        for action_label in sorted(by_action, key=action_sort_key):
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
        for action_label in sorted(by_action, key=action_sort_key):
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
    conn.close()

    actions, embargoed = build_actions(per_type_rows)
    grouped = group_actions(actions)

    out_path = (Path(sys.argv[2]) if len(sys.argv) > 2
                else Path(__file__).resolve().parent.parent / 'reports' / 'og_status_action_items.txt')
    out_path.parent.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().astimezone().isoformat(timespec='seconds')
    out_path.write_text(render_text(grouped, embargoed, generated_at))

    distinct_ogs = len(set(a[2] for a in actions))
    print(f"Written to {out_path} ({len(actions)} action entries across {distinct_ogs} OGs)")

    print_summary(grouped)


if __name__ == '__main__':
    main()
