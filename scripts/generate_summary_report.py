#!/usr/bin/env python3
"""Quarterly summary report — headline counts for sharing with management.

Reads a combined og_status CSV (as produced by combined_upload_status.py) and renders a
short PDF summary (needs `pip3 install --user fpdf2`), with one section per genome type:
reference genomes, draft genomes, mitogenomes.

Each section is one table of three rows — Total OGs, Assemblies, Raw data — against four
mutually exclusive columns, so every row adds up to that type's OG count:

    Published    an accession is on record at ENA/NCBI. Whether it has gone public yet
                 (the ERZ -> GCA step) is deliberately ignored here.
    In progress  sample.embargo_status is 'Release' but nothing is submitted yet.
    Embargo      sample.embargo_status is 'Embargo' and nothing is submitted yet.
    Embargo/Release unknown   no embargo decision recorded against the sample.

GenomeArk sits beside those as a subset of Published — how many are also on the open-data
bucket — and is NOT part of the sum. Draft genomes have no GenomeArk channel, and
mitogenome submissions carry no raw reads, so those cells read '-'.

An OG with no record at all for a given type (hasn't reached that stage yet) isn't counted
in that type's section. Per-technology raw detail (HiFi, Hi-C, ONT, Illumina) and the
ERZ/GCA split stay in og_status.csv rather than the PDF.

This is a derived, presentation-only view — it does not re-query the database. Run
combined_upload_status.py (or run_combined_report.sh, which does this automatically)
first to produce the CSV it reads.

Only the PDF is archived quarter to quarter (not the source CSV) — so each PDF embeds its
own totals in its document metadata (the PDF's Keywords field — not shown when the file is
opened/printed). The next quarter's run reads that back out of the previous archived PDF
(needs `pip3 install --user pypdf`) to compute the "since last report" deltas, rather than
needing the original CSV.

Usage:
    python3 generate_summary_report.py <current.csv> <output.pdf> [previous.pdf]

    If previous.pdf is omitted, the script looks in ../reports/archive/ for the most
    recently archived summary PDF and uses that automatically (skipped if none exists yet).
"""
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import CLASSIFY

SECTIONS = [
    ('ref', 'Reference genomes'),
    ('draft', 'Draft genomes'),
    ('mito', 'Mitogenomes'),
]

# The three rows of each section. Every row partitions that type's whole OG count into
# Published / In progress / Embargo / Embargo-Release unknown, so each row sums to the
# section total. GenomeArk is a subset of Published and is not part of that sum.
MEASURES = [
    ('Total OGs', 'total'),
    ('Assemblies', 'assemblies'),
    ('Raw data', 'raw'),
]

# Mitogenome submissions carry no raw reads, so that cell has nothing to report.
def applicable(genome_type, measure):
    return not (genome_type == 'mito' and measure == 'raw')


def load(csv_path):
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    generated_at = rows[0]['generated_at'] if rows else None
    return rows, generated_at


def truthy(value):
    return str(value).strip().lower() == 'true'


def is_published(row, t, measure):
    """Published means an accession is on record for that data, whether or not it has gone
    public yet - the ERZ/GCA distinction is deliberately ignored here."""
    assemblies = truthy(row.get(f'{t}_assembly_published'))
    raw = truthy(row.get(f'{t}_raw_submitted')) if t != 'mito' else False
    return {'assemblies': assemblies, 'raw': raw, 'total': assemblies or raw}[measure]


def on_genomeark(row, t, measure):
    """Published to the open-data bucket. Draft genomes have no such channel."""
    if t == 'ref':
        assemblies = truthy(row.get('s3_assemblies'))
        raw = truthy(row.get('s3_raw_hifi')) or truthy(row.get('s3_raw_hic'))
    elif t == 'mito':
        assemblies, raw = truthy(row.get('s3_mito')), False
    else:
        return None
    return {'assemblies': assemblies, 'raw': raw, 'total': assemblies or raw}[measure]


def summarize(rows):
    per_type = {}
    for t, _ in SECTIONS:
        recs = [r for r in rows if CLASSIFY[t](r.get(f'{t}_upload_status')) != 'no_record']
        measures = {}
        for _, measure in MEASURES:
            if not applicable(t, measure):
                measures[measure] = None
                continue
            c = Counter()
            for row in recs:
                if is_published(row, t, measure):
                    c['published'] += 1
                    if on_genomeark(row, t, measure):
                        c['genomeark'] += 1
                    continue
                embargo = row.get('embargo_status')
                if embargo == 'Release':
                    c['in_progress'] += 1
                elif embargo == 'Embargo':
                    c['embargo'] += 1
                else:
                    c['unknown'] += 1
            measures[measure] = c
        per_type[t] = {
            'total': len(recs),
            'measures': measures,
            'cohort': Counter(r.get('mito_cohort') or 'unknown' for r in recs) if t == 'mito' else None,
        }

    # Distinct OGs, not a sum across sections: a mitogenome is published under the same
    # species/ToLID directory as its reference genome, so summing would count it twice.
    ark_cols = ('s3_assemblies', 's3_mito', 's3_raw_hifi', 's3_raw_hic')
    genomeark_ogs = sum(1 for r in rows if any(truthy(r.get(c)) for c in ark_cols))

    return {
        'total': len(rows),
        'total_published': sum(per_type[t]['measures']['total']['published'] for t, _ in SECTIONS),
        'total_genomeark': genomeark_ogs,
        'per_type': per_type,
    }


def find_previous(out_path):
    archive_dir = Path(out_path).resolve().parent / 'archive'
    if not archive_dir.is_dir():
        return None
    candidates = sorted(p for p in archive_dir.glob('*.pdf') if p.resolve() != Path(out_path).resolve())
    return candidates[-1] if candidates else None


DATA_MARKER = 'OCOM_SUMMARY_DATA_V4'
LEGACY_MARKERS = ('OCOM_SUMMARY_DATA_V4', 'OCOM_SUMMARY_DATA_V3', 'OCOM_SUMMARY_DATA_V2')


def flatten(summary):
    """The counts embedded in the PDF for next quarter's deltas."""
    flat = {
        'total': summary['total'],
        'total_published': summary['total_published'],
        'total_genomeark': summary['total_genomeark'],
    }
    for t, _ in SECTIONS:
        flat[f'{t}_total'] = summary['per_type'][t]['total']
        for _, measure in MEASURES:
            c = summary['per_type'][t]['measures'][measure]
            if c is None:
                continue
            flat[f'{t}_{measure}_published'] = c['published']
            flat[f'{t}_{measure}_genomeark'] = c['genomeark']
    return flat


def encode_summary(summary, generated_at):
    tokens = [f'generated_at={generated_at}'] + [f'{k}={v}' for k, v in flatten(summary).items()]
    return DATA_MARKER + ' ' + ' '.join(tokens)


def extract_previous(pdf_path):
    """Read a previous summary PDF's embedded counts back out. Accepts V2 (totals only)
    and V3; any count the older format didn't carry simply has no delta."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
        keywords = (reader.metadata or {}).get('/Keywords') or ''
    except Exception:
        return None

    for marker in LEGACY_MARKERS:
        m = re.match(re.escape(marker) + r'\b(.*)', keywords, re.S)
        if m:
            break
    else:
        return None

    data = {}
    for tok in m.group(1).split():
        if '=' in tok:
            k, v = tok.split('=', 1)
            data[k] = v
    if 'total' not in data:
        return None

    counts = {}
    for k, v in data.items():
        if k == 'generated_at':
            continue
        try:
            counts[k] = int(v)
        except ValueError:
            pass
    return {'generated_at': data.get('generated_at'), 'counts': counts}


TEXT_RGB = (25, 28, 26)
MUTED_RGB = (105, 112, 108)
BORDER_RGB = (205, 209, 203)
RULE_RGB = (60, 64, 61)
HEADER_FILL_RGB = (242, 243, 240)
UP_RGB = (35, 110, 85)
DOWN_RGB = (163, 62, 40)

CARD_PAD = 5


def render_pdf(current, previous, out_path):
    from fpdf import FPDF

    now_label = datetime.now().strftime('%B %Y')
    generated_label = current['generated_at']
    summary = current['summary']
    flat = flatten(summary)
    prev_counts = previous['counts'] if previous else {}
    prev_generated_label = previous['generated_at'] if previous else None

    def delta_for(key):
        if key not in prev_counts or key not in flat:
            return None
        d = flat[key] - prev_counts[key]
        if d == 0:
            return '(=)', MUTED_RGB
        return f"({'+' if d > 0 else ''}{d})", (UP_RGB if d > 0 else DOWN_RGB)

    pdf = FPDF(format='A4', unit='mm')
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_keywords(encode_summary(summary, generated_label))
    pdf.add_page()
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font('Times', 'B', 21)
    pdf.set_text_color(*TEXT_RGB)
    pdf.cell(0, 9, 'OceanGenomes Upload Status', new_x='LMARGIN', new_y='NEXT')

    pdf.set_font('Helvetica', 'I', 10.5)
    pdf.set_text_color(*MUTED_RGB)
    pdf.cell(0, 6, f'Quarterly Data Sharing Summary - {now_label}', new_x='LMARGIN', new_y='NEXT')

    pdf.set_draw_color(*RULE_RGB)
    pdf.set_line_width(0.6)
    y = pdf.get_y() + 1.5
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_y(y + 3)

    pdf.set_font('Helvetica', '', 8.5)
    pdf.set_text_color(*MUTED_RGB)
    pdf.cell(0, 5, f'Generated {generated_label}', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)

    # --- headline cards -------------------------------------------------------------
    cards = [
        (flat['total'], 'Distinct OGs found', delta_for('total')),
        (flat['total_published'], 'Published (draft + reference + mito)', delta_for('total_published')),
        (flat['total_genomeark'], 'OGs on GenomeArk', delta_for('total_genomeark')),
    ]
    box_gap = 5
    box_w = (page_w - box_gap * (len(cards) - 1)) / len(cards)
    box_h = 24
    y0 = pdf.get_y()
    for i, (num, label, delta) in enumerate(cards):
        box_x = pdf.l_margin + i * (box_w + box_gap)
        pdf.set_draw_color(*BORDER_RGB)
        pdf.set_line_width(0.3)
        pdf.rect(box_x, y0, box_w, box_h, 'D')
        pdf.set_xy(box_x + CARD_PAD, y0 + CARD_PAD)
        pdf.set_font('Helvetica', 'B', 17)
        pdf.set_text_color(*TEXT_RGB)
        pdf.cell(box_w - 2 * CARD_PAD, 8, str(num), new_x='LEFT', new_y='NEXT')
        pdf.set_x(box_x + CARD_PAD)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(*MUTED_RGB)
        pdf.cell(box_w - 2 * CARD_PAD, 4.5, label, new_x='LEFT', new_y='NEXT')
        if delta:
            pdf.set_x(box_x + CARD_PAD)
            pdf.set_font('Helvetica', '', 7.5)
            pdf.set_text_color(*delta[1])
            pdf.cell(box_w - 2 * CARD_PAD, 4, delta[0])
    pdf.set_y(y0 + box_h + 8)

    # --- helpers --------------------------------------------------------------------
    def ensure_room(h):
        if pdf.get_y() + h > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_xy(pdf.l_margin, pdf.t_margin)

    def table(headers, body, label_w=40):
        """body: list of (row label, [(value, delta_key or None), ...])"""
        ensure_room(9 * (len(body) + 1) + 4)
        col_w = (page_w - label_w) / (len(headers) - 1)
        x0 = pdf.l_margin
        row_h = 8
        # A header too wide for its column wraps onto a second line at '\n'.
        head_h = 11 if any('\n' in h for h in headers) else row_h
        pdf.set_fill_color(*HEADER_FILL_RGB)
        y_head = pdf.get_y()
        pdf.rect(x0, y_head, page_w, head_h, 'F')
        pdf.set_font('Helvetica', 'B', 7.5)
        pdf.set_text_color(*TEXT_RGB)
        pdf.set_xy(x0, y_head)
        pdf.cell(label_w, head_h, headers[0])
        for i, h in enumerate(headers[1:]):
            cx = x0 + label_w + i * col_w
            lines = h.split('\n')
            for j, part in enumerate(lines):
                pdf.set_xy(cx, y_head + (head_h - 4.2 * len(lines)) / 2 + j * 4.2)
                pdf.cell(col_w, 4.2, part, align='R')
        pdf.set_y(y_head + head_h)
        pdf.set_draw_color(*RULE_RGB)
        pdf.set_line_width(0.4)
        pdf.line(x0, pdf.get_y(), x0 + page_w, pdf.get_y())

        for row_label, cells in body:
            pdf.set_font('Helvetica', '', 9)
            pdf.set_text_color(*TEXT_RGB)
            pdf.set_x(x0)
            pdf.cell(label_w, row_h, row_label)
            for value, delta_key in cells:
                delta = delta_for(delta_key) if delta_key else None
                text = str(value)
                # Every cell sets its own font and colour: a delta leaves them changed.
                pdf.set_font('Helvetica', '', 9)
                pdf.set_text_color(*TEXT_RGB)
                if not delta:
                    pdf.cell(col_w, row_h, text, align='R')
                    continue
                num_w = pdf.get_string_width(text) + 1
                pdf.set_font('Helvetica', '', 7)
                d_w = pdf.get_string_width(delta[0]) + 1
                pad = col_w - num_w - d_w
                if pad > 0:
                    pdf.cell(pad, row_h, '')
                pdf.set_font('Helvetica', '', 9)
                pdf.cell(num_w, row_h, text, align='R')
                pdf.set_font('Helvetica', '', 7)
                pdf.set_text_color(*delta[1])
                pdf.cell(d_w, row_h, delta[0], align='R')
            pdf.ln(row_h)
            pdf.set_draw_color(*BORDER_RGB)
            pdf.set_line_width(0.2)
            pdf.line(x0, pdf.get_y(), x0 + page_w, pdf.get_y())

    def heading(text):
        ensure_room(14)
        pdf.ln(5)
        pdf.set_font('Helvetica', 'B', 11.5)
        pdf.set_text_color(*TEXT_RGB)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 7, text, new_x='LMARGIN', new_y='NEXT')

    def note_line(text):
        pdf.set_font('Helvetica', 'I', 7.5)
        pdf.set_text_color(*MUTED_RGB)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(page_w, 4, text, new_x='LMARGIN', new_y='NEXT')

    # --- one section per genome type ------------------------------------------------
    headers = ['', 'Published', 'GenomeArk', 'In progress', 'Embargo', 'Embargo/Release\nunknown']
    for t, label in SECTIONS:
        info = summary['per_type'][t]
        heading(f"{label}  ({info['total']} OGs)")

        body = []
        for row_label, measure in MEASURES:
            c = info['measures'][measure]
            if c is None:
                body.append((row_label, [('-', None)] * (len(headers) - 1)))
                continue
            ga = c['genomeark'] if t != 'draft' else '-'
            body.append((row_label, [
                (c['published'], f'{t}_{measure}_published'),
                (ga, f'{t}_{measure}_genomeark' if t != 'draft' else None),
                (c['in_progress'], None),
                (c['embargo'], None),
                (c['unknown'], None),
            ]))
        table(headers, body, label_w=30)

        if t == 'mito':
            cohort = info['cohort']
            note_line(f"No raw reads: mitogenome submissions carry assemblies only - the reads reach "
                      f"ENA through the reference- or draft-genome pipeline that sequenced the OG. "
                      f"{cohort.get('reference', 0)} of these OGs are reference genomes, "
                      f"{cohort.get('draft', 0)} are draft genomes, "
                      f"{cohort.get('unknown', 0)} are neither on record.")
        elif t == 'draft':
            note_line('Draft genomes are not published to GenomeArk.')

    # --- what the columns mean ------------------------------------------------------
    heading('Note')
    for term, meaning in [
        ('Published', 'an accession is recorded for that data at ENA/NCBI.'),
        ('GenomeArk', 'also published to the open-data bucket. This is a subset of Published, so it '
                      'is not added into the row.'),
        ('In progress', 'cleared for release, but nothing submitted yet - the actionable backlog.'),
        ('Embargo', 'under embargo and not submitted.'),
        ('Embargo/Release unknown', 'no embargo decision is recorded against the sample.'),
    ]:
        ensure_room(9)
        pdf.set_x(pdf.l_margin)
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(*TEXT_RGB)
        w = pdf.get_string_width(term) + 2
        pdf.cell(w, 4.6, term)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(*MUTED_RGB)
        pdf.multi_cell(page_w - w, 4.6, meaning, new_x='LMARGIN', new_y='NEXT')
    pdf.ln(1)

    # The footer sits inside the bottom margin, so it must not trigger a page break.
    pdf.set_auto_page_break(False)
    pdf.set_y(-20)
    pdf.set_draw_color(*BORDER_RGB)
    pdf.set_line_width(0.3)
    fy = pdf.get_y()
    pdf.line(pdf.l_margin, fy, pdf.w - pdf.r_margin, fy)
    pdf.set_y(fy + 2.5)
    pdf.set_font('Helvetica', '', 7.5)
    pdf.set_text_color(*MUTED_RGB)
    pdf.cell(page_w, 4, generated_label, align='R')

    pdf.output(str(out_path))


def main():
    if len(sys.argv) < 3:
        sys.exit('Usage: generate_summary_report.py <current.csv> <output.pdf> [previous.pdf]')
    current_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    previous_path = Path(sys.argv[3]) if len(sys.argv) > 3 else find_previous(out_path)

    current_rows, current_generated_at = load(current_path)
    current = {
        'generated_at': current_generated_at,
        'summary': summarize(current_rows),
    }

    previous = extract_previous(previous_path) if previous_path and previous_path.exists() else None

    render_pdf(current, previous, out_path)
    print(f"Written to {out_path}")
    if previous:
        print(f"Compared against {previous_path}")
    else:
        print("No previous report found to compare against (pass one explicitly, or run again next quarter).")


if __name__ == '__main__':
    main()
