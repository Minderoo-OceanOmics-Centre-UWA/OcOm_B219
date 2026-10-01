#!/usr/bin/env python3
"""Open-data release state, read from the S3 species bucket.

refgen-compile publishes reference genomes to the open-data bucket and records
nothing in the database, so the listing is the only source of truth for what is
publicly downloadable. Rows are matched on species AND ToLID together, never ToLID
alone. Draft genomes have no open-data channel.

Usage:
    python3 public_release.py [listing.txt]     # refresh the listing, then summarise
"""

import re
import subprocess
import sys
from pathlib import Path

REMOTE = 'opendata:minderoo-oceanomics/species/'
EMPTY = {'found': False, 'assemblies': False, 'mito': False, 'raw_hifi': False, 'raw_hic': False}


def _norm(value):
    return re.sub(r'\s+', '_', str(value or '').strip()).casefold()


def fetch(path):
    """Refresh the listing from the bucket. Needs rclone and network access."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ['rclone', 'lsf', '-R', '--dirs-only', '--max-depth', '4', REMOTE],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rclone listing of {REMOTE} failed: {result.stderr.strip()}")
    path.write_text(result.stdout)
    return path


def load(path):
    """Parse a listing into {(species, tolid): {subdirectory, ...}}."""
    entries = {}
    for line in Path(path).read_text().splitlines():
        parts = [p for p in line.strip().strip('/').split('/') if p]
        if len(parts) < 2:
            continue
        key = (_norm(parts[0]), _norm(parts[1]))
        subs = entries.setdefault(key, set())
        if len(parts) > 2:
            subs.add('/'.join(parts[2:]))
    return entries


def lookup(entries, species, tol_id):
    subs = entries.get((_norm(species), _norm(tol_id)))
    if subs is None:
        return dict(EMPTY)
    return {
        'found': True,
        'assemblies': any(s.startswith(('assembly_curated', 'assembly_oceangenomes')) for s in subs),
        'mito': any(s.startswith('assembly_MT') for s in subs),
        'raw_hifi': 'genomic_data/pacbio_hifi' in subs,
        'raw_hic': 'genomic_data/dovetail' in subs,
    }


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'reports' / 'public_release_listing.txt'
    print(f"Refreshing listing from {REMOTE} -> {path}")
    fetch(path)
    entries = load(path)
    counts = {k: 0 for k in ('assemblies', 'mito', 'raw_hifi', 'raw_hic')}
    for species, tol_id in entries:
        flags = lookup(entries, species, tol_id)
        for k in counts:
            counts[k] += bool(flags[k])
    print(f"{len(entries)} published (species, ToLID) pair(s)")
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == '__main__':
    main()
