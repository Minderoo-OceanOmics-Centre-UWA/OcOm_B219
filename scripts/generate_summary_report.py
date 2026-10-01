#!/usr/bin/env python3
"""Quarterly summary report — headline counts for sharing with management.

Reads a combined og_status CSV (as produced by combined_upload_status.py) and renders a
short PDF summary (needs `pip3 install --user fpdf2`), with one section per genome type.
Each section separates assemblies from raw sequencing data, and ENA/SRA submission from
what is publicly downloadable:

    Reference genomes  assemblies (ERZ -> GCA -> open-data bucket)
                       raw data per technology (HiFi, Hi-C, ONT, Illumina)
    Draft genomes      assemblies (ERZ -> GCA); Illumina raw. No open-data channel.
    Mitogenomes        assemblies only — no raw reads are submitted by that pipeline.
                       Split by whether the OG is a reference- or draft-genome OG.

"Uploaded" per type follows queries.py's CLASSIFY, so the definition lives in one place;
"public" follows PUBLIC_STATUS. An accession existing means submitted — release state is
reported alongside, never as a precondition. "Released"/"Embargoed" for a whole OG comes
from sample.embargo_status. An OG with no record at all for a given type (hasn't reached
that stage yet) isn't counted in that type's section.

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
from queries import CLASSIFY, PUBLIC_STATUS

SECTIONS = [
    ('ref', 'Reference genomes'),
    ('draft', 'Draft genomes'),
    ('mito', 'Mitogenomes'),
]

# Raw sequencing data columns per type, in the order they should be presented.
RAW_TECHS = {
    'ref': [('hifi', 'HiFi'), ('hic', 'Hi-C'), ('ont', 'ONT'), ('illumina', 'Illumina')],
    'draft': [('illumina', 'Illumina')],
    'mito': [],
}

# Which open-data bucket folder evidences each type's assemblies, and each raw technology.
S3_ASSEMBLY_COL = {'ref': 's3_assemblies', 'mito': 's3_mito', 'draft': None}
S3_RAW_COL = {'hifi': 's3_raw_hifi', 'hic': 's3_raw_hic'}


def load(csv_path):
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    generated_at = rows[0]['generated_at'] if rows else None
    return rows, generated_at


def raw_state(value):
    """Collapse a per-technology release state ('public', 'embargoed+public', '') to one
    of None (nothing submitted), 'public', 'embargoed' or 'unknown'."""
    if not value:
        return None
    parts = set(value.split('+'))
    if parts == {'public'}:
        return 'public'
    if 'embargoed' in parts:
        return 'embargoed'
    return 'unknown'


def truthy(value):
    return str(value).strip().lower() == 'true'


def summarize(rows):
    per_type = {}
    for t, _ in SECTIONS:
        counts = Counter()
        raw = {tech: Counter() for tech, _ in RAW_TECHS[t]}
        cohort = Counter()
        for row in rows:
            status = row.get(f'{t}_upload_status')
            bucket = CLASSIFY[t](status)
            if bucket == 'no_record':
                continue
            counts['total'] += 1
            if bucket == 'uploaded':
                counts['uploaded'] += 1
            if status == PUBLIC_STATUS[t]:
                counts['public'] += 1

            embargo = row.get('embargo_status')
            if embargo == 'Embargo':
                counts['embargoed'] += 1
            elif embargo == 'Release':
                counts['release_uploaded' if bucket == 'uploaded' else 'release_in_progress'] += 1

            s3_col = S3_ASSEMBLY_COL[t]
            if s3_col and truthy(row.get(s3_col)):
                counts['s3'] += 1

            if t == 'mito':
                cohort[row.get('mito_cohort') or 'unknown'] += 1

            for tech, _ in RAW_TECHS[t]:
                if t == 'draft':
                    # draft_genomes.sra_accession carries no release state, only presence.
                    if truthy(row.get('draft_raw_submitted')):
                        raw[tech]['submitted'] += 1
                    continue
                state = raw_state(row.get(f'{t}_raw_{tech}'))
                if state is None:
                    continue
                raw[tech]['submitted'] += 1
                raw[tech][state] += 1
                s3_raw = S3_RAW_COL.get(tech)
                if s3_raw and truthy(row.get(s3_raw)):
                    raw[tech]['s3'] += 1

        per_type[t] = {'counts': counts, 'raw': raw, 'cohort': cohort}

    total_uploaded = sum(per_type[t]['counts']['uploaded'] for t, _ in SECTIONS)
    total_public = sum(per_type[t]['counts']['public'] for t, _ in SECTIONS)
    return {
        'total': len(rows),
        'total_uploaded': total_uploaded,
        'total_public': total_public,
        'per_type': per_type,
    }


def find_previous(out_path):
    archive_dir = Path(out_path).resolve().parent / 'archive'
    if not archive_dir.is_dir():
        return None
    candidates = sorted(p for p in archive_dir.glob('*.pdf') if p.resolve() != Path(out_path).resolve())
    return candidates[-1] if candidates else None


DATA_MARKER = 'OCOM_SUMMARY_DATA_V3'
LEGACY_MARKERS = ('OCOM_SUMMARY_DATA_V3', 'OCOM_SUMMARY_DATA_V2')


def flatten(summary):
    """The counts embedded in the PDF for next quarter's deltas."""
    flat = {
        'total': summary['total'],
        'total_uploaded': summary['total_uploaded'],
        'total_public': summary['total_public'],
    }
    for t, _ in SECTIONS:
        c = summary['per_type'][t]['counts']
        for key in ('total', 'uploaded', 'public', 's3'):
            flat[f'{t}_{key}'] = c[key]
        for tech, _ in RAW_TECHS[t]:
            flat[f'{t}_raw_{tech}'] = summary['per_type'][t]['raw'][tech]['submitted']
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
    note = f'Generated {generated_label}'
    if previous:
        note += f'   |   Compared against the report generated {prev_generated_label}'
    pdf.cell(0, 5, note, new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)

    # --- headline cards -------------------------------------------------------------
    cards = [
        (flat['total'], 'Total OGs found', delta_for('total')),
        (flat['total_uploaded'], 'Submitted (draft + reference + mito)', delta_for('total_uploaded')),
        (flat['total_public'], 'Publicly available', delta_for('total_public')),
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
        pdf.set_fill_color(*HEADER_FILL_RGB)
        pdf.rect(x0, pdf.get_y(), page_w, row_h, 'F')
        pdf.set_font('Helvetica', 'B', 7.5)
        pdf.set_text_color(*TEXT_RGB)
        pdf.set_x(x0)
        pdf.cell(label_w, row_h, headers[0])
        for h in headers[1:]:
            pdf.cell(col_w, row_h, h, align='R')
        pdf.ln(row_h)
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
                if delta:
                    pdf.set_font('Helvetica', '', 9)
                    pdf.set_text_color(*TEXT_RGB)
                    num_w = pdf.get_string_width(text) + 1
                    d_w = 0
                    pdf.set_font('Helvetica', '', 7)
                    d_w = pdf.get_string_width(delta[0]) + 1
                    pad = col_w - num_w - d_w
                    if pad > 0:
                        pdf.cell(pad, row_h, '')
                    pdf.set_font('Helvetica', '', 9)
                    pdf.set_text_color(*TEXT_RGB)
                    pdf.cell(num_w, row_h, text, align='R')
                    pdf.set_font('Helvetica', '', 7)
                    pdf.set_text_color(*delta[1])
                    pdf.cell(d_w, row_h, delta[0], align='R')
                else:
                    pdf.cell(col_w, row_h, text, align='R')
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
    for t, label in SECTIONS:
        c = summary['per_type'][t]['counts']
        raw = summary['per_type'][t]['raw']
        heading(label)

        asm_headers = ['Assemblies', 'Total OGs', 'Submitted', 'Public (GCA)']
        asm_cells = [(c['total'], f'{t}_total'), (c['uploaded'], f'{t}_uploaded'),
                     (c['public'], f'{t}_public')]
        if S3_ASSEMBLY_COL[t]:
            asm_headers.append('Open data')
            asm_cells.append((c['s3'], f'{t}_s3'))
        asm_headers += ['In progress', 'Embargo']
        asm_cells += [(c['release_in_progress'], None), (c['embargoed'], None)]
        table(asm_headers, [('All', asm_cells)], label_w=26)

        if RAW_TECHS[t]:
            pdf.ln(2)
            if t == 'draft':
                table(['Raw data', 'Submitted'],
                      [(name, [(raw[tech]['submitted'], f'{t}_raw_{tech}')])
                       for tech, name in RAW_TECHS[t]])
                note_line('Draft genomes have no open-data bucket channel, and '
                          'draft_genomes.sra_accession records submission only, not release state.')
            else:
                table(['Raw data', 'Submitted', 'Public at ENA', 'Embargoed', 'Unknown', 'Open data'],
                      [(name, [
                          (raw[tech]['submitted'], f'{t}_raw_{tech}'),
                          (raw[tech]['public'], None),
                          (raw[tech]['embargoed'], None),
                          (raw[tech]['unknown'], None),
                          (raw[tech]['s3'] if tech in S3_RAW_COL else '-', None),
                      ]) for tech, name in RAW_TECHS[t]])
        if t == 'mito':
            cohort = summary['per_type'][t]['cohort']
            pdf.ln(2)
            table(['Belongs to', 'OGs'],
                  [('Reference genome OGs', [(cohort.get('reference', 0), None)]),
                   ('Draft genome OGs', [(cohort.get('draft', 0), None)]),
                   ('Neither on record', [(cohort.get('unknown', 0), None)])])
            note_line('Mitogenome submissions carry assemblies only - raw reads reach ENA through '
                      'the reference- or draft-genome pipeline that sequenced the OG.')

    # --- appendix -------------------------------------------------------------------
    pdf.set_auto_page_break(False)
    ensure_room(14)
    pdf.ln(6)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(*TEXT_RGB)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 7, 'Reference genomes submitted', new_x='LMARGIN', new_y='NEXT')

    half_w = page_w / 2
    entry_h = 4.6
    all_rows = current.get('rows', [])
    uploaded_rows = sorted(
        (r for r in all_rows if CLASSIFY['ref'](r.get('ref_upload_status')) == 'uploaded'),
        key=lambda r: r['og_id']
    )
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
    ensure_room(10)
    note_line(f"Draft genomes ({summary['per_type']['draft']['counts']['uploaded']}) and mitogenomes "
              f"({summary['per_type']['mito']['counts']['uploaded']}) are too numerous to list here - "
              f"see og_status.csv for the full per-OG breakdown.")

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
