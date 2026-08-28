#!/usr/bin/env python3
"""Quarterly summary report — headline counts for sharing with management.

Reads a combined og_status CSV (as produced by combined_upload_status.py) and
renders a short PDF summary (needs `pip3 install --user fpdf2`):
    - Total OGs found, and total genome records uploaded (draft + reference +
      mitogenome combined).
    - Per genome type: total OGs found for that type, and how many of those
      are released and fully uploaded, released but still in progress, or
      under embargo.

"Uploaded" per type follows each type's own upload_status value (see
draft_genome_upload_status.py / ref_genome_upload_status.py /
mitogenome_upload_status.py docstrings for the full value list):
    draft: "all uploaded" -> uploaded; anything else -> in progress.
    ref:   "all uploaded" -> uploaded; anything else -> in progress.
    mito:  "<tech> uploaded" -> uploaded; "needs_uploading" -> in progress.
"Released"/"Embargoed" comes from the OG's sample.embargo_status. An OG with
no record at all for a given type (hasn't reached that stage yet) isn't
counted in that type's row.

This is a derived, presentation-only view — it does not re-query the
database. Run combined_upload_status.py (or run_combined_report.sh, which
does this automatically) first to produce the CSV it reads.

Only the PDF is archived quarter to quarter (not the source CSV) — so each
PDF embeds its own totals in its document metadata (the PDF's Keywords
field — not shown when the file is opened/printed). The next quarter's run
reads that back out of the previous archived PDF (needs
`pip3 install --user pypdf`) to compute the "since last report" deltas,
rather than needing the original CSV.

Usage:
    python3 generate_summary_report.py <current.csv> <output.pdf> [previous.pdf]

    If previous.pdf is omitted, the script looks in ../reports/archive/ for
    the most recently archived summary PDF and uses that automatically
    (skipped if none exists yet).
"""
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import TYPES, CLASSIFY


def load(csv_path):
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    generated_at = rows[0]['generated_at'] if rows else None
    return rows, generated_at


def summarize(rows):
    type_total = Counter()
    uploaded_count = Counter()
    release_uploaded = Counter()
    release_in_progress = Counter()
    embargoed = Counter()

    for row in rows:
        embargo = row.get('embargo_status')
        for t, _ in TYPES:
            status = row.get(f'{t}_upload_status')
            bucket = CLASSIFY[t](status)
            if bucket == 'no_record':
                continue
            type_total[t] += 1
            if bucket == 'uploaded':
                uploaded_count[t] += 1
            if embargo == 'Embargo':
                embargoed[t] += 1
            elif embargo == 'Release':
                if bucket == 'uploaded':
                    release_uploaded[t] += 1
                else:
                    release_in_progress[t] += 1

    total_uploaded = sum(uploaded_count[t] for t, _ in TYPES)
    return {
        'total': len(rows),
        'total_uploaded': total_uploaded,
        'type_total': type_total,
        'release_uploaded': release_uploaded,
        'release_in_progress': release_in_progress,
        'embargoed': embargoed,
    }


def find_previous(out_path):
    archive_dir = Path(out_path).resolve().parent / 'archive'
    if not archive_dir.is_dir():
        return None
    candidates = sorted(p for p in archive_dir.glob('*.pdf') if p.resolve() != Path(out_path).resolve())
    return candidates[-1] if candidates else None


DATA_MARKER = 'OCOM_SUMMARY_DATA_V2'


def encode_summary(summary, generated_at):
    tokens = [
        f'generated_at={generated_at}',
        f'total={summary["total"]}',
        f'total_uploaded={summary["total_uploaded"]}',
    ]
    return DATA_MARKER + ' ' + ' '.join(tokens)


def extract_previous(pdf_path):
    """Read a previous summary PDF's embedded metadata back into a summary dict."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
        keywords = (reader.metadata or {}).get('/Keywords') or ''
    except Exception:
        return None

    m = re.match(re.escape(DATA_MARKER) + r'\b(.*)', keywords, re.S)
    if not m:
        return None

    data = {}
    for tok in m.group(1).split():
        if '=' in tok:
            k, v = tok.split('=', 1)
            data[k] = v
    if 'total' not in data:
        return None

    return {
        'generated_at': data.get('generated_at'),
        'summary': {
            'total': int(data['total']),
            'total_uploaded': int(data.get('total_uploaded', 0)),
        },
    }


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
    prev = previous['summary'] if previous else None
    prev_generated_label = previous['generated_at'] if previous else None

    def delta_str(cur, prv):
        if prv is None:
            return None
        d = cur - prv
        if d == 0:
            return '(no change)', MUTED_RGB
        sign = '+' if d > 0 else ''
        return f'({sign}{d})', (UP_RGB if d > 0 else DOWN_RGB)

    pdf = FPDF(format='A4', unit='mm')
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_keywords(encode_summary(current['summary'], generated_label))
    pdf.add_page()
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    # --- Header ---
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
    note = f'Generated {generated_label}'
    if prev:
        note += f'   |   Compared against the report generated {prev_generated_label}'
    pdf.cell(0, 5, note, new_x='LMARGIN', new_y='NEXT')
    pdf.ln(6)

    # --- Totals: two bordered stat boxes ---
    total_delta = delta_str(current['summary']['total'], prev['total']) if prev else None
    uploaded_delta = delta_str(current['summary']['total_uploaded'], prev['total_uploaded']) if prev else None

    box_gap = 6
    box_w = (page_w - box_gap) / 2
    box_h = 24
    y0 = pdf.get_y()

    for i, (num, label, delta) in enumerate([
        (current['summary']['total'], 'Total OGs found', total_delta),
        (current['summary']['total_uploaded'], 'Total uploaded (draft + reference + mito)', uploaded_delta),
    ]):
        box_x = pdf.l_margin + i * (box_w + box_gap)
        pdf.set_draw_color(*BORDER_RGB)
        pdf.set_line_width(0.3)
        pdf.rect(box_x, y0, box_w, box_h, 'D')
        pdf.set_xy(box_x + CARD_PAD, y0 + CARD_PAD)
        pdf.set_font('Helvetica', 'B', 17)
        pdf.set_text_color(*TEXT_RGB)
        pdf.cell(box_w - 2 * CARD_PAD, 8, str(num), new_x='LEFT', new_y='NEXT')
        pdf.set_x(box_x + CARD_PAD)
        pdf.set_font('Helvetica', '', 8.5)
        pdf.set_text_color(*MUTED_RGB)
        pdf.cell(box_w - 2 * CARD_PAD, 4.5, label, new_x='LEFT', new_y='NEXT')
        if delta:
            pdf.set_x(box_x + CARD_PAD)
            pdf.set_font('Helvetica', '', 7.5)
            pdf.set_text_color(*delta[1])
            pdf.cell(box_w - 2 * CARD_PAD, 4, delta[0])

    pdf.set_y(y0 + box_h + 10)

    # --- Per-type table ---
    col_type_w = 34
    col_w = (page_w - col_type_w) / 4
    row_h = 9
    x0 = pdf.l_margin

    headers = ['Genome type', 'Total OGs found', 'Release & uploaded', 'Release & in progress', 'Embargo']

    pdf.set_fill_color(*HEADER_FILL_RGB)
    pdf.rect(x0, pdf.get_y(), page_w, row_h, 'F')
    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_text_color(*TEXT_RGB)
    pdf.set_x(x0)
    pdf.cell(col_type_w, row_h, headers[0])
    for h in headers[1:]:
        pdf.cell(col_w, row_h, h, align='R')
    pdf.ln(row_h)

    pdf.set_draw_color(*RULE_RGB)
    pdf.set_line_width(0.4)
    ly = pdf.get_y()
    pdf.line(x0, ly, x0 + page_w, ly)

    for t, label in TYPES:
        pdf.set_font('Helvetica', '', 9.5)
        pdf.set_text_color(*TEXT_RGB)
        pdf.set_x(x0)
        pdf.cell(col_type_w, row_h, label)

        for val in (
            current['summary']['type_total'][t],
            current['summary']['release_uploaded'][t],
            current['summary']['release_in_progress'][t],
            current['summary']['embargoed'][t],
        ):
            pdf.cell(col_w, row_h, str(val), align='R')
        pdf.ln(row_h)

        pdf.set_draw_color(*BORDER_RGB)
        pdf.set_line_width(0.2)
        ly = pdf.get_y()
        pdf.line(x0, ly, x0 + page_w, ly)

    # --- Uploaded OGs appendix (FYI) ---
    # Two-column layout with manual pagination: fpdf2's auto page break only
    # triggers correctly for straight top-to-bottom flows, not for cells we
    # position ourselves with set_xy(), so we pre-check room before each row.
    pdf.set_auto_page_break(False)

    def ensure_room(h):
        if pdf.get_y() + h > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_xy(pdf.l_margin, pdf.t_margin)

    ensure_room(12)
    pdf.ln(8)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(*TEXT_RGB)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 7, 'List of uploaded OGs', new_x='LMARGIN', new_y='NEXT')

    half_w = page_w / 2
    entry_h = 4.6
    all_rows = current.get('rows', [])
    types_by_key = dict(TYPES)
    appendix_order = [(k, types_by_key[k]) for k in ('ref', 'mito', 'draft')]
    for t, label in appendix_order:
        uploaded_rows = sorted(
            (r for r in all_rows if CLASSIFY[t](r.get(f'{t}_upload_status')) == 'uploaded'),
            key=lambda r: r['og_id']
        )
        ensure_room(entry_h + 3)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(*TEXT_RGB)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 6, f'{label} ({len(uploaded_rows)})', new_x='LMARGIN', new_y='NEXT')

        for i in range(0, len(uploaded_rows), 2):
            pair = uploaded_rows[i:i + 2]
            ensure_room(entry_h)
            y = pdf.get_y()
            for col, r in enumerate(pair):
                species = r.get('species') or '-'
                pdf.set_xy(pdf.l_margin + col * half_w, y)
                pdf.set_font('Helvetica', '', 8)
                pdf.set_text_color(*TEXT_RGB)
                og_w = pdf.get_string_width(r['og_id']) + 2
                pdf.cell(og_w, entry_h, r['og_id'])
                pdf.set_font('Helvetica', 'I', 8)
                pdf.set_text_color(*MUTED_RGB)
                pdf.cell(half_w - og_w - 4, entry_h, species)
            pdf.set_y(y + entry_h)

        pdf.ln(3)

    # --- Footer ---
    # footer sits right at the bottom margin; auto page break already off above
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
        'rows': current_rows,
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
