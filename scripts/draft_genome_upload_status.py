#!/usr/bin/env python3
"""Draft genome upload status report.

One row per og_id, using the seq_date that matches an ilmn lca_validation record,
else the latest. Full SQL: queries.py (DRAFT_SQL).

Usage:
    singularity run $SING/psycopg2:0.1.sif python draft_genome_upload_status.py \
        ~/postgresql_details/oceanomics.cfg

    Writes a timestamped CSV + flagged-only CSV to ../reports/.
"""
import configparser, psycopg2, csv, sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import DRAFT_SQL as sql

cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
p = cfg["postgres"]
conn = psycopg2.connect(dbname=p["dbname"], user=p["user"], password=p["password"],
                        host=p["host"], port=int(p["port"]))
cur = conn.cursor()

cur.execute(sql)
rows = cur.fetchall()
cols = [d[0] for d in cur.description]

stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
out_path = Path(__file__).resolve().parent.parent / "reports" / f"draft_status_{stamp}.csv"
out_path.parent.mkdir(parents=True, exist_ok=True)

with open(out_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    writer.writerows(rows)
print(f"Written to {out_path}")

print(f"\nTotal: {len(rows)} OGs")
for status, count in sorted(Counter(dict(zip(cols, r))['upload_status'] for r in rows).items()):
    print(f"  {status}: {count}")

flag_idx = cols.index('data_quality_flag')
flagged_rows = [r for r in rows if r[flag_idx]]

flag_path = out_path.with_name(f"{out_path.stem}_data_quality_issues{out_path.suffix}")
with open(flag_path, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(cols)
    writer.writerows(flagged_rows)
print(f"\nData quality issues ({len(flagged_rows)} rows) written to {flag_path}")

conn.close()
