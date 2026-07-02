"""
Post-processor for index.html (runs after each sync from DailyEquityScanner).

As of 2026-07, the DailyEquityScanner source now natively produces most of the
enhancements this script used to apply (section ordering/numbering, Plotly in
<head>, collapsible sections, HQ %-from-spot axis, ROIC/FCF in the Conviction
Map, company names in Quality × Valuation, and FCF-scaled bubbles, plus the page
navigation bar). Those transforms have been removed as redundant.

What remains here is only what the source does NOT do:
  1. Sort the Signal Matrix heatmap rows by blended conviction (descending).
  2. Inject the COT Positioning Regime (from COT_Analysis_Latest.html, a
     different repo) into the "Macro & Regime" section, plus its CSS.
  3. Add a navigation bar to COT_Analysis_Latest.html so it can link back to
     the Daily Brief (the COT-Analysis repo does not ship one).

Usage:
  python3 inject_quality_metrics.py index.html [COT_Analysis_Latest.html] [--cot-only]

  --cot-only  Skip the Signal Matrix sort; only apply COT injection and the
              COT-page nav. Used by the Friday COT sync job, where index.html is
              already processed and only the COT content needs refreshing.
"""
import os
import re
import sys

COT_BLOCK_CSS = (
    '.cot-regime{margin-top:16px;padding:12px 16px;border-left:3px solid #6a4ca5;'
    'background:rgba(106,76,165,.05);border-radius:0 4px 4px 0;}'
    '.cot-regime h4{margin:0 0 8px;font-size:11px;text-transform:uppercase;'
    'letter-spacing:.06em;color:#6a4ca5;font-family:sans-serif;}'
    '.cot-regime .cr-title{font-weight:600;margin:0 0 8px;}'
    '.cot-regime blockquote{margin:0 0 10px;padding:6px 10px;'
    'border-left:2px solid rgba(106,76,165,.3);font-style:italic;font-size:12px;}'
    '.cr-sub{font-size:11px;font-weight:600;margin:8px 0 4px;text-transform:uppercase;'
    'letter-spacing:.04em;color:var(--faint,#6b7280);}'
    'details.cr-confirm>summary.cr-sub{list-style:none;cursor:pointer;}'
    'details.cr-confirm>summary.cr-sub::-webkit-details-marker{display:none;}'
    'details.cr-confirm>summary.cr-sub::after{content:" \\25b8";font-size:10px;'
    'margin-left:4px;}'
    'details.cr-confirm[open]>summary.cr-sub::after{content:" \\25be";}'
    '.cot-tbl{width:100%;border-collapse:collapse;font-size:12px;margin:0 0 8px;}'
    '.cot-tbl th,.cot-tbl td{padding:4px 8px;border:1px solid rgba(31,42,51,.12);text-align:left;}'
    '.cot-tbl th{background:rgba(31,42,51,.06);font-weight:600;}'
)

COT_NAV_HTML = (
    '<nav class="page-nav">'
    '<a href="index.html">Daily Brief</a>'
    '<a href="COT_Analysis_Latest.html" class="nav-cur">COT Analysis</a>'
    '</nav>'
)

COT_NAV_CSS = (
    '.page-nav{display:flex;gap:0;border-bottom:1px solid #ddd;margin-bottom:12px;}'
    '.page-nav a{padding:8px 16px;font-family:Georgia,"Times New Roman",serif;'
    'font-size:13px;text-decoration:none;color:#6b7280;'
    'border-bottom:2px solid transparent;margin-bottom:-1px;}'
    '.page-nav a:hover{color:#1f2a33;}'
    '.page-nav a.nav-cur{color:#1f2a33;font-weight:600;border-bottom-color:#1f2a33;}'
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _section_display_title(section_html):
    m = re.search(r'<span class="n">\d+</span>(.*?)</h2>', section_html)
    return m.group(1).strip() if m else ''


def _all_sections(html):
    return list(re.finditer(r'<section>.*?</section>', html, re.DOTALL))


def _extract_div_balanced(html, start):
    """Return the full div starting at `start`, respecting nested divs."""
    depth = 0
    i = start
    n = len(html)
    while i < n:
        if html[i:i+4] == '<div' and (i + 4 >= n or html[i + 4] in ' \t\n>'):
            depth += 1
            gt = html.find('>', i)
            i = gt + 1 if gt != -1 else n
        elif html[i:i+6] == '</div>':
            depth -= 1
            i += 6
            if depth == 0:
                return html[start:i]
        else:
            i += 1
    return html[start:]


# ── 1. sort Signal Matrix rows by mean-z descending ──────────────────────────

def _parse_nested_array(s):
    return [[x.strip() for x in row.split(',')]
            for row in re.findall(r'\[([^\[\]]+)\]', s)]


def _row_mean(tokens):
    vals = [0.0 if t == 'null' else float(t) for t in tokens]
    return sum(vals) / len(vals) if vals else 0.0


def _fmt_nested_floats(rows):
    return '[' + ','.join('[' + ','.join(r) + ']' for r in rows) + ']'


def _parse_nested_strings(s):
    return [re.findall(r'"([^"]+)"', row)
            for row in re.findall(r'\[([^\[\]]+)\]', s)]


def _fmt_nested_strings(rows):
    return '[' + ','.join('[' + ','.join(f'"{v}"' for v in r) + ']' for r in rows) + ']'


def sort_signal_matrix(html):
    segs = _all_sections(html)
    sm_seg = next(
        (s for s in segs if 'Signal Matrix' in _section_display_title(s.group(0))),
        None)
    if not sm_seg:
        print('WARNING: Signal Matrix section not found', file=sys.stderr)
        return html

    sm = sm_seg.group(0)

    y_m    = re.search(r'"y":\[([^\]]+)\]', sm)
    z_m    = re.search(r'"z":(\[\[.*?\]\])', sm, re.DOTALL)
    text_m = re.search(r'"text":(\[\[.*?\]\])', sm, re.DOTALL)
    if not (y_m and z_m and text_m):
        print('WARNING: Signal Matrix arrays not found', file=sys.stderr)
        return html

    y    = re.findall(r'"([^"]+)"', y_m.group(1))
    z    = _parse_nested_array(z_m.group(1))
    text = _parse_nested_strings(text_m.group(1))

    if not (len(y) == len(z) == len(text)):
        return html

    order = sorted(range(len(y)), key=lambda i: _row_mean(z[i]), reverse=True)
    if order == list(range(len(y))):
        return html  # already sorted — idempotent

    y    = [y[i]    for i in order]
    z    = [z[i]    for i in order]
    text = [text[i] for i in order]

    new_sm = sm
    new_sm = (new_sm[:y_m.start()] + '"y":[' + ','.join(f'"{t}"' for t in y) + ']'
              + new_sm[y_m.end():])

    z_m2 = re.search(r'"z":(\[\[.*?\]\])', new_sm, re.DOTALL)
    new_sm = new_sm[:z_m2.start(1)] + _fmt_nested_floats(z) + new_sm[z_m2.end(1):]

    t_m2 = re.search(r'"text":(\[\[.*?\]\])', new_sm, re.DOTALL)
    new_sm = new_sm[:t_m2.start(1)] + _fmt_nested_strings(text) + new_sm[t_m2.end(1):]

    return html[:sm_seg.start()] + new_sm + html[sm_seg.end():]


# ── 2. COT Positioning Regime → Macro & Regime section ───────────────────────

def extract_cot_regime_block(cot_html):
    """
    Extract the Positioning Regime card from COT HTML and return a formatted
    HTML block for injection.  Two sub-sections are included:
      1. Regime title + description blockquote + Dashboard Output table
      2. Confirmation layers table (collapsible)
    """
    card_start = cot_html.find('<div class="commentary regime-card">')
    if card_start == -1:
        print('WARNING: regime-card div not found in COT HTML', file=sys.stderr)
        return None
    card = _extract_div_balanced(cot_html, card_start)

    title_m = re.search(r'<p><strong>(#\d+[^<]+?)</strong></p>', card)
    title   = title_m.group(1).rstrip('.') if title_m else 'Positioning Regime'

    bq_m   = re.search(r'<blockquote>(.*?)</blockquote>', card, re.DOTALL)
    bq_html = f'<blockquote>{bq_m.group(1)}</blockquote>' if bq_m else ''

    tables = re.findall(r'<table[^>]*>.*?</table>', card, re.DOTALL)

    # Sub-section 1: regime name + description + Dashboard Output
    s1 = f'<p class="cr-title">{title}</p>{bq_html}'
    if tables:
        s1 += ('<p class="cr-sub">Dashboard Output</p>'
               + tables[0].replace('class="cmt-tbl"', 'class="cot-tbl"'))

    # Sub-section 2: Confirmation layers (collapsed by default)
    s2 = ''
    if len(tables) > 1:
        confirm_m = re.search(r'<strong>(Confirmation layers[^<]*?)</strong>', card)
        confirm_label = (confirm_m.group(1).split('—')[0].strip()
                         if confirm_m else 'Confirmation layers')
        s2 = (f'<details class="cr-confirm">'
              f'<summary class="cr-sub">{confirm_label}</summary>'
              + tables[1].replace('class="cmt-tbl"', 'class="cot-tbl"')
              + '</details>')

    return (f'<div class="cot-regime">'
            f'<h4>COT Positioning Regime</h4>'
            f'{s1}{s2}'
            f'</div>')


def inject_cot_into_macro_regime(index_html, cot_block):
    """Replace (or add) the COT block inside the Macro & Regime section."""
    segs = _all_sections(index_html)
    macro_seg = next(
        (s for s in segs if 'Macro' in _section_display_title(s.group(0))),
        None)
    if not macro_seg:
        print('WARNING: Macro & Regime section not found', file=sys.stderr)
        return index_html

    section = macro_seg.group(0)
    # Remove any previously injected COT block (idempotent)
    section = re.sub(r'<div class="cot-regime">.*?</div>', '', section,
                     flags=re.DOTALL)
    new_section = section.replace('</section>', cot_block + '</section>', 1)
    return index_html[:macro_seg.start()] + new_section + index_html[macro_seg.end():]


def inject_cot_css(html):
    """Ensure the COT block CSS is present in index.html (idempotent)."""
    if COT_BLOCK_CSS in html or '</style>' not in html:
        return html
    return html.replace('</style>', COT_BLOCK_CSS + '</style>', 1)


# ── 3. Navigation bar for the COT page ───────────────────────────────────────

def add_cot_nav(html):
    """Inject nav bar CSS and HTML into COT_Analysis_Latest.html (idempotent)."""
    if 'page-nav' in html:  # quote-insensitive: already has a nav
        return html
    if '</style>' in html:
        html = html.replace('</style>', COT_NAV_CSS + '</style>', 1)
    else:
        html = html.replace('<body>', f'<style>{COT_NAV_CSS}</style><body>', 1)
    html = html.replace('<body>', '<body>' + COT_NAV_HTML, 1)
    return html


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    raw_args  = sys.argv[1:]
    cot_only  = '--cot-only' in raw_args
    pos_args  = [a for a in raw_args if not a.startswith('--')]

    path     = pos_args[0] if pos_args else 'index.html'
    cot_path = pos_args[1] if len(pos_args) > 1 else None

    # Auto-detect adjacent COT file when no explicit path given
    if cot_path is None:
        base_dir  = os.path.dirname(os.path.abspath(path))
        candidate = os.path.join(base_dir, 'COT_Analysis_Latest.html')
        if os.path.exists(candidate):
            cot_path = candidate

    html = open(path, encoding='utf-8').read()

    if not cot_only:
        html = sort_signal_matrix(html)
        print('✓ Signal Matrix sorted by conviction', file=sys.stderr)

    # COT Positioning Regime injection (from the COT-Analysis repo)
    cot_html = None
    if cot_path and os.path.exists(cot_path):
        cot_html = open(cot_path, encoding='utf-8').read()
        cot_block = extract_cot_regime_block(cot_html)
        if cot_block:
            html = inject_cot_css(html)
            html = inject_cot_into_macro_regime(html, cot_block)
            print('✓ COT Positioning Regime injected into Macro & Regime',
                  file=sys.stderr)

    open(path, 'w', encoding='utf-8').write(html)

    if cot_html is not None and cot_path:
        cot_html = add_cot_nav(cot_html)
        open(cot_path, 'w', encoding='utf-8').write(cot_html)
        print('✓ Nav bar added to COT_Analysis_Latest.html', file=sys.stderr)

    print('Done.', file=sys.stderr)


if __name__ == '__main__':
    main()
