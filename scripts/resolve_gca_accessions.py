#!/usr/bin/env python3
"""Fill the public GCA_ accession ENA assigns once a submitted assembly has been processed.

ENA issues the GCA hours to days after it accepts an ERZ, so the submission pipelines can
only record the ERZ. This fills in the GCA for all three genome types in one pass, as part
of the quarterly report run, rather than running each pipeline's own resolver separately.

    ref    ref_genomes_assembly_uploads.gca_accession_hap1 / _hap2
    draft  draft_genomes.assembly_accession
    mito   ena_submissions.ena_assembly_accession

Each type is matched against the ENA Portal API differently, because of how its assemblies
are organised into studies:

    ref    every haplotype has its own BioProject, so one query over those studies returns
           one assembly per haplotype. Falls back to BioSample + "_haplotype_N_" in the
           assembly name when bioproject_hapN is blank.
    draft  all drafts sit in one study, so assemblies are matched on BioSample, with the
           OG id in the assembly name as a tie-breaker.
    mito   every mitogenome has its own component BioProject; assemblies are matched on
           assembly_name, which Webin-CLI sets from the manifest NAME (the full_seqid).
           The BioSample is checked as a sanity guard, not used for matching.

Writes are BLANK-ONLY - an existing accession is never overwritten; a differing value is
reported as a conflict and left alone.

Usage:
    singularity run $SING/psycopg2:0.1.sif python resolve_gca_accessions.py \\
        ~/postgresql_details/oceanomics.cfg [--types ref,draft,mito] [--dry-run]
"""

import argparse
import configparser
import csv
import io
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg2

PORTAL = "https://www.ebi.ac.uk/ena/portal/api/search"
DRAFT_STUDY = "PRJEB123829"
CHUNK = 40     # studies per Portal query, keeps the URL short
RETRIES = 4    # the Portal intermittently answers 200 with a truncated body
FIELDS = 'accession,assembly_name,sample_accession,study_accession,version'


def load_db_config(config_file):
    if not Path(config_file).exists():
        raise FileNotFoundError(f"❌ Config file '{config_file}' does not exist.")
    config = configparser.ConfigParser()
    config.read(config_file)
    if not config.has_section('postgres'):
        raise ValueError("❌ Missing [postgres] section in config file.")
    return {
        'dbname':   config.get('postgres', 'dbname'),
        'user':     config.get('postgres', 'user'),
        'password': config.get('postgres', 'password'),
        'host':     config.get('postgres', 'host'),
        'port':     config.getint('postgres', 'port'),
    }


def portal_rows(params):
    """One Portal query parsed as TSV. The Portal intermittently answers 200 with the rows
    replaced by 'ERROR occurred. Not all results may have been written.' Parsing that as TSV
    yields no assemblies, which would be reported as 'not yet processed by ENA' - so retry,
    and fail loudly rather than under-report."""
    q = urllib.parse.urlencode(params)
    for attempt in range(1, RETRIES + 1):
        with urllib.request.urlopen(f"{PORTAL}?{q}", timeout=120) as r:
            text = r.read().decode()
        if 'ERROR occurred' not in text:
            return list(csv.DictReader(io.StringIO(text), delimiter='\t'))
        print(f"⚠️  Portal returned a truncated result ({attempt}/{RETRIES}), retrying")
        time.sleep(5 * attempt)
    sys.exit("❌ ENA Portal kept returning truncated results - nothing written, try again later.")


def fetch_assemblies(field, values):
    """Processed assemblies whose `field` is one of `values`, keyed by that field."""
    out = {}
    for i in range(0, len(values), CHUNK):
        chunk = values[i:i + CHUNK]
        for a in portal_rows({
            'result': 'assembly',
            'query': ' OR '.join(f'{field}="{v}"' for v in chunk),
            'fields': FIELDS,
            'format': 'tsv',
            'limit': 0,
        }):
            out.setdefault(a[field], []).append(a)
    return out


def full_accession(hit):
    """ENA reports the accession and its version separately."""
    gca = hit['accession']
    ver = (hit.get('version') or '').strip()
    return f"{gca}.{ver}" if ver and '.' not in gca else gca


class Report:
    """What happened to each candidate, so a run can be read at a glance."""

    def __init__(self, label, unit):
        self.label, self.unit = label, unit
        self.written, self.pending, self.conflicts, self.ambiguous, self.skipped = [], [], [], [], []

    def summarise(self, dry_run):
        tag = "would write" if dry_run else "wrote"
        print(f"\n=== {self.label} ===")
        print(f"✅ {tag} GCA for {len(self.written)} {self.unit}(s)")
        for name, gca in self.written:
            print(f"   {name}\t{gca}")
        print(f"⏳ {len(self.pending)} submitted but not yet processed by ENA: "
              f"{', '.join(self.pending) or '-'}")
        if self.skipped:
            print(f"⚠️  {len(self.skipped)} skipped (nothing to match on): {', '.join(self.skipped)}")
        if self.conflicts:
            print(f"⚠️  {len(self.conflicts)} conflict(s) (existing value differs, NOT changed):")
            for name, old, new in self.conflicts:
                print(f"   {name}\tDB={old}\tENA={new}")
        if self.ambiguous:
            print(f"⚠️  {len(self.ambiguous)} with several candidate assemblies, NOT written:")
            for name, accs in self.ambiguous:
                print(f"   {name}\t{', '.join(accs)}")


def write_blank_only(cur, conn, sql, params, label, dry_run):
    if dry_run:
        return
    cur.execute(sql, params)
    if cur.rowcount != 1:
        conn.rollback()
        sys.exit(f"❌ unexpected rowcount {cur.rowcount} for {label}")


def resolve_ref(cur, conn, dry_run):
    rep = Report('Reference genomes', 'haplotype')
    cur.execute("""
        SELECT og_id, biosample, bioproject_hap1, bioproject_hap2,
               assembly_accession_hap1, assembly_accession_hap2,
               gca_accession_hap1, gca_accession_hap2
        FROM ref_genomes_assembly_uploads
        WHERE assembly_accession_hap1 LIKE 'ERZ%%' OR assembly_accession_hap2 LIKE 'ERZ%%'
        ORDER BY og_id
    """)
    rows = cur.fetchall()
    print(f"🗄️  ref: {len(rows)} assembly-upload row(s) with an ERZ")

    targets = []
    for og_id, sample, bp1, bp2, erz1, erz2, gca1, gca2 in rows:
        for hap, bp, erz, gca in ((1, bp1, erz1, gca1), (2, bp2, erz2, gca2)):
            if erz and erz.startswith('ERZ'):
                targets.append((og_id, hap, (sample or '').strip(), (bp or '').strip(), (gca or '').strip()))

    studies = sorted({bp for _, _, _, bp, _ in targets if bp})
    by_study = fetch_assemblies('study_accession', studies) if studies else {}
    fallback_samples = sorted({s for _, _, s, bp, _ in targets if not bp and s})
    by_sample = fetch_assemblies('sample_accession', fallback_samples) if fallback_samples else {}

    for og_id, hap, sample, study, existing in targets:
        name = f"{og_id}.hap{hap}"
        if study:
            cands = by_study.get(study, [])
        elif sample:
            cands = [c for c in by_sample.get(sample, []) if f"_haplotype_{hap}_" in c['assembly_name']]
        else:
            rep.skipped.append(name)
            continue
        if not cands:
            rep.pending.append(name)
            continue
        if len(cands) > 1:
            rep.ambiguous.append((name, [c['accession'] for c in cands]))
            continue
        gca_full = full_accession(cands[0])
        if existing:
            if existing not in (cands[0]['accession'], gca_full):
                rep.conflicts.append((name, existing, gca_full))
            continue
        col = f"gca_accession_hap{hap}"
        write_blank_only(
            cur, conn,
            f"UPDATE ref_genomes_assembly_uploads SET {col}=%s "
            f"WHERE og_id=%s AND NULLIF({col},'') IS NULL",
            (gca_full, og_id), name, dry_run)
        rep.written.append((name, gca_full))
    return rep


def resolve_draft(cur, conn, dry_run, study=DRAFT_STUDY):
    rep = Report('Draft genomes', 'OG')
    assemblies = portal_rows({
        'result': 'assembly',
        'query': f'study_accession="{study}"',
        'fields': FIELDS,
        'format': 'tsv',
        'limit': 0,
    })
    by_sample = {}
    for a in assemblies:
        by_sample.setdefault(a['sample_accession'], []).append(a)

    cur.execute("""
        SELECT og_id, seq_date, biosample_accession, ena_analysis_accession, assembly_accession
        FROM draft_genomes
        WHERE NULLIF(ena_analysis_accession, '') IS NOT NULL
        ORDER BY og_id
    """)
    rows = cur.fetchall()
    print(f"🗄️  draft: {len(rows)} row(s) with an ERZ; ENA lists {len(assemblies)} assembl(ies) in {study}")

    for og_id, seq_date, biosample, _erz, existing in rows:
        cands = by_sample.get(biosample or '', [])
        if len(cands) > 1:
            # Same BioSample used for >1 assembly; prefer the one named for this OG.
            named = [c for c in cands if f"_{og_id}_" in c['assembly_name']]
            cands = named or cands
        if not cands:
            rep.pending.append(og_id)
            continue
        if len(cands) > 1:
            rep.ambiguous.append((og_id, [c['accession'] for c in cands]))
            continue
        gca_full = full_accession(cands[0])
        if existing and existing.strip():
            if existing.strip() not in (cands[0]['accession'], gca_full):
                rep.conflicts.append((og_id, existing, gca_full))
            continue
        write_blank_only(
            cur, conn,
            "UPDATE draft_genomes SET assembly_accession=%s "
            "WHERE og_id=%s AND seq_date=%s AND NULLIF(assembly_accession,'') IS NULL",
            (gca_full, og_id, seq_date), f"{og_id} {seq_date}", dry_run)
        rep.written.append((og_id, gca_full))
    return rep


def resolve_mito(cur, conn, dry_run, webin_mode='production'):
    rep = Report('Mitogenomes', 'assembly')
    cur.execute("""
        SELECT full_seqid, og_id, ena_study_accession, biosample_accession,
               ena_analysis_accession, ena_assembly_accession
        FROM ena_submissions
        WHERE webin_mode = %s
          AND submission_status = 'ACCESSION_ASSIGNED'
          AND NULLIF(ena_analysis_accession, '') IS NOT NULL
        ORDER BY og_id
    """, (webin_mode,))
    rows = cur.fetchall()

    studies = sorted({r[2] for r in rows if r[2]})
    by_study = fetch_assemblies('study_accession', studies) if studies else {}
    by_name = {}
    for cands in by_study.values():
        for a in cands:
            by_name.setdefault(a['assembly_name'].strip(), []).append(a)
    print(f"🗄️  mito: {len(rows)} submission(s) with an ERZ across {len(studies)} stud(ies)")

    for full_seqid, _og_id, _study, biosample, _erz, existing in rows:
        cands = by_name.get(full_seqid, [])
        if not cands:
            rep.pending.append(full_seqid)
            continue
        if len(cands) > 1:
            rep.ambiguous.append((full_seqid, [c['accession'] for c in cands]))
            continue
        hit = cands[0]
        if biosample and hit['sample_accession'] and hit['sample_accession'] != biosample:
            # Same name under a different sample: not ours to guess about.
            rep.conflicts.append((full_seqid, f"BioSample {biosample}", f"ENA {hit['sample_accession']}"))
            continue
        gca_full = full_accession(hit)
        if existing and existing.strip():
            if existing.strip() not in (hit['accession'], gca_full):
                rep.conflicts.append((full_seqid, existing, gca_full))
            continue
        write_blank_only(
            cur, conn,
            "UPDATE ena_submissions SET ena_assembly_accession=%s, updated_at=CURRENT_TIMESTAMP "
            "WHERE full_seqid=%s AND webin_mode=%s AND NULLIF(ena_assembly_accession,'') IS NULL",
            (gca_full, full_seqid, webin_mode), full_seqid, dry_run)
        rep.written.append((full_seqid, gca_full))
    return rep


RESOLVERS = {'ref': resolve_ref, 'draft': resolve_draft, 'mito': resolve_mito}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('config', help='Path to oceanomics.cfg')
    ap.add_argument('--types', default='ref,draft,mito',
                    help='Comma-separated subset of ref,draft,mito (default: all)')
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would be written, change nothing')
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(',') if t.strip()]
    unknown = [t for t in types if t not in RESOLVERS]
    if unknown:
        sys.exit(f"❌ unknown type(s): {', '.join(unknown)}. Choose from ref, draft, mito.")

    conn = psycopg2.connect(**load_db_config(args.config))
    cur = conn.cursor()
    reports = [RESOLVERS[t](cur, conn, args.dry_run) for t in types]
    if not args.dry_run:
        conn.commit()
    conn.close()

    for rep in reports:
        rep.summarise(args.dry_run)

    total = sum(len(r.written) for r in reports)
    print(f"\n{'Would write' if args.dry_run else 'Wrote'} {total} GCA accession(s) in total.")


if __name__ == '__main__':
    main()
