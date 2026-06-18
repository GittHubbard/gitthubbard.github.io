"""
Post-processor for index.html (runs after each sync from DailyEquityScanner).

Transformations applied in order:
  1. Move "High-Quality Buys" from last to second named section; renumber.
  2. Hoist Plotly <script> tags into <head> so HQ Buys chart loads correctly.
  3. Sort Signal Matrix heatmap rows by blended conviction descending.
  4. Collapse Signal Matrix, What Changed Overnight, Master Signal Table,
     and Earnings Runway.
  5. Convert HQ Buys chart x-axis from $ to % from spot (per stock).
  6. Inject ROIC % · FCF % from Quality × Valuation into Conviction Map tooltips.
  7. Inject COT Positioning Regime into Macro & Regime (when COT file present).
  8. Add page navigation bar to index.html and COT_Analysis_Latest.html.

Usage:
  python3 inject_quality_metrics.py index.html [COT_Analysis_Latest.html] [--cot-only]

  --cot-only  Skip structural transforms (1-6); only apply COT injection and
              navigation. Use when index.html is already processed and only the
              COT content / nav needs to be refreshed.
"""
import os
import re
import sys

BR  = '\\u003cbr\\u003e'
DOT = '\\u00b7'

COLLAPSIBLE_CSS = (
    'details.sec-body>summary{list-style:none;cursor:pointer;user-select:none;}'
    'details.sec-body>summary::-webkit-details-marker{display:none;}'
    'details.sec-body>summary h2::after{content:" \\25b8";color:var(--faint);'
    'font-size:11px;font-family:sans-serif;vertical-align:middle;margin-left:6px;}'
    'details.sec-body[open]>summary h2::after{content:" \\25be";}'
)

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

NAV_CSS = (
    '.page-nav{display:flex;gap:0;border-bottom:1px solid rgba(31,42,51,.12);margin:0 0 4px;}'
    '.page-nav a{padding:8px 16px;font-family:Georgia,"Times New Roman",serif;'
    'font-size:13px;text-decoration:none;color:var(--faint,#6b7280);'
    'border-bottom:2px solid transparent;margin-bottom:-1px;}'
    '.page-nav a:hover{color:var(--text,#1f2a33);}'
    '.page-nav a.nav-cur{color:var(--text,#1f2a33);font-weight:600;'
    'border-bottom-color:var(--text,#1f2a33);}'
)

INDEX_NAV_HTML = (
    '<nav class="page-nav">'
    '<a href="index.html" class="nav-cur">Daily Brief</a>'
    '<a href="COT_Analysis_Latest.html">COT Analysis</a>'
    '</nav>'
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

COLLAPSE_TITLES = {'Signal Matrix', 'What Changed Overnight', 'Master Signal Table',
                   'Earnings Runway'}


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


# ── 0. hoist Plotly scripts into <head> ──────────────────────────────────────

def hoist_plotly_to_head(html):
    config_pat = re.compile(r'<script>window\.PlotlyConfig\s*=.*?</script>')
    cdn_pat    = re.compile(
        r'<script\s[^>]*src="https://cdn\.plot\.ly[^"]*"[^>]*></script>')

    config_tags = config_pat.findall(html)
    cdn_tags    = cdn_pat.findall(html)

    if not cdn_tags:
        return html

    # Check if already in <head>
    head_end = html.find('</head>')
    if head_end != -1 and cdn_tags and cdn_tags[0] in html[:head_end]:
        return html  # already hoisted — idempotent

    for tag in config_tags:
        html = html.replace(tag, '', 1)
    for tag in cdn_tags:
        html = html.replace(tag, '', 1)

    inject = ''.join(config_tags[:1]) + ''.join(cdn_tags[:1])
    html = html.replace('</head>', inject + '</head>', 1)
    return html


# ── 1. move HQ Buys to slot 2 and renumber ───────────────────────────────────

def move_hq_and_renumber(html):
    segs = _all_sections(html)
    bodies = [s.group(0) for s in segs]

    named = bodies[1:]  # bodies[0] is unnumbered chips row

    hq_idx = next(
        (i for i, b in enumerate(named) if 'High-Quality' in _section_display_title(b)),
        None)
    if hq_idx is None:
        print('WARNING: High-Quality Buys section not found', file=sys.stderr)
        return html

    hq = named.pop(hq_idx)
    named.insert(1, hq)

    renumbered = []
    for i, body in enumerate(named, 1):
        body = re.sub(r'<span class="n">\d+</span>',
                      f'<span class="n">{i:02d}</span>', body, count=1)
        renumbered.append(body)

    pre  = html[:segs[0].start()]
    post = html[segs[-1].end():]
    return pre + bodies[0] + ''.join(renumbered) + post


# ── 2. sort Signal Matrix rows by mean-z descending ──────────────────────────

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


# ── 3. collapse specified sections ───────────────────────────────────────────

def make_collapsible(html):
    def wrap(m):
        inner = m.group(1)
        if inner.startswith('<details class="sec-body">'):
            return m.group(0)  # already collapsed — idempotent
        title = _section_display_title(inner).replace('&amp;', '&')
        if title not in COLLAPSE_TITLES:
            return m.group(0)
        h2_m = re.search(r'<h2>.*?</h2>', inner)
        if not h2_m:
            return m.group(0)
        h2   = h2_m.group(0)
        rest = inner[h2_m.end():]
        return (f'<section>'
                f'<details class="sec-body">'
                f'<summary>{h2}</summary>'
                f'{rest}'
                f'</details>'
                f'</section>')

    return re.sub(r'<section>(.*?)</section>', wrap, html, flags=re.DOTALL)


def inject_css(html):
    if COLLAPSIBLE_CSS in html:
        return html  # idempotent
    return html.replace('</style>', COLLAPSIBLE_CSS + '</style>', 1)


# ── 4. ROIC / FCF injection into Conviction Map ──────────────────────────────

def build_quality_lookup(html):
    s = re.search(r'Quality\s*[×x]\s*Valuation.*?(?=<h2>)', html, re.DOTALL)
    if not s:
        print('WARNING: Quality × Valuation section not found', file=sys.stderr)
        return {}

    lookup = {}
    for block in re.findall(r'"hovertext":\[(.*?)\]', s.group(0), re.DOTALL):
        for item in re.findall(r'"(\\u003cb\\u003e.*?)"', block):
            tm = re.match(r'\\u003cb\\u003e(\w+)\\u003c\\u002fb\\u003e', item)
            if not tm:
                continue
            ticker = tm.group(1)
            parts  = item.split('\\u003cbr\\u003e')
            if len(parts) < 3:
                continue
            rm = re.search(r'ROIC\s+(\S+)', parts[1])
            fm = re.search(r'^FCF\s+(\S+)',  parts[2])
            if rm and fm:
                lookup[ticker] = f'ROIC {rm.group(1)} {DOT} FCF {fm.group(1)}'
    return lookup


def inject_quality_metrics(html, lookup):
    if not lookup:
        return html

    cm = re.search(r'Conviction Map.*?(?=<h2>)', html, re.DOTALL)
    if not cm:
        return html

    def patch(m):
        item = m.group(1)
        tm = re.match(r'\\u003cb\\u003e(\w+)\\u003c', item)
        if not tm or tm.group(1) not in lookup:
            return m.group(0)
        ticker = tm.group(1)
        if lookup[ticker] in item:  # already injected — idempotent
            return m.group(0)
        patched, n = re.subn(
            r'(\\u003cbr\\u003eUpside.*?Technicals \w+)',
            lambda mo: mo.group(1) + BR + lookup[ticker],
            item, count=1)
        return f'"{patched}"' if n else m.group(0)

    new_cm = re.sub(r'"(\\u003cb\\u003e[^"]+)"', patch, cm.group(0))
    return html[:cm.start()] + new_cm + html[cm.end():]


# ── 5. HQ Buys chart: x-axis → % from spot ──────────────────────────────────

def _split_trace_objects(data_str):
    traces, depth, start = [], 0, None
    for i, c in enumerate(data_str):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                traces.append((start, i + 1))
    return traces


def _first_ticker(trace):
    ym = re.search(r'"y":\[([^\]]*)\]', trace)
    if not ym:
        return None
    names = re.findall(r'"([^"]+)"', ym.group(1))
    return names[0] if names else None


def convert_hq_xaxis_to_pct(html):
    segs = _all_sections(html)
    hq_seg = next(
        (s for s in segs if 'High-Quality' in _section_display_title(s.group(0))),
        None)
    if not hq_seg:
        print('WARNING: High-Quality Buys section not found', file=sys.stderr)
        return html

    hq = hq_seg.group(0)

    # Skip if already converted (idempotent)
    if '"Distance from spot' in hq:
        return html

    np_m = re.search(r'Plotly\.newPlot\(\s*"[^"]+",\s*', hq)
    if not np_m:
        print('WARNING: HQ newPlot call not found', file=sys.stderr)
        return html
    arr_start = hq.index('[', np_m.end())
    depth = 0
    arr_end = None
    for i in range(arr_start, len(hq)):
        if hq[i] == '[':
            depth += 1
        elif hq[i] == ']':
            depth -= 1
            if depth == 0:
                arr_end = i + 1
                break
    if arr_end is None:
        return html

    data = hq[arr_start:arr_end]
    spans = _split_trace_objects(data)
    traces = [data[a:b] for a, b in spans]

    spot = {}
    for t in traces:
        if '"symbol":"diamond"' in t:
            ticker = _first_ticker(t)
            xm = re.search(r'"x":\[([^\]]*)\]', t)
            if ticker and xm:
                try:
                    spot[ticker] = float(xm.group(1).split(',')[0])
                except ValueError:
                    pass

    if not spot:
        print('WARNING: no spot diamonds found in HQ chart', file=sys.stderr)
        return html

    def convert(trace):
        ticker = _first_ticker(trace)
        if ticker not in spot or spot[ticker] == 0:
            return trace
        s = spot[ticker]

        xm = re.search(r'"x":\[([^\]]+)\]', trace)
        if not xm:
            return trace
        orig = [float(v) for v in xm.group(1).split(',')]
        pct = [(v - s) / s * 100 for v in orig]
        trace = (trace[:xm.start()]
                 + '"x":[' + ','.join(repr(p) for p in pct) + ']'
                 + trace[xm.end():])

        if '"hovertemplate"' in trace and 'customdata' not in trace:
            cd = '"customdata":[' + ','.join(repr(v) for v in orig) + '],'
            trace = trace.replace('"hovertemplate":', cd + '"hovertemplate":', 1)
            trace = trace.replace(
                '$%{x:.2f}', '$%{customdata:.2f} (%{x:+.1f}% from spot)', 1)
        return trace

    new_traces = [convert(t) for t in traces]

    new_data = data[:spans[0][0]]
    for idx, (a, b) in enumerate(spans):
        new_data += new_traces[idx]
        sep_end = spans[idx + 1][0] if idx + 1 < len(spans) else len(data)
        new_data += data[b:sep_end]

    new_hq = hq[:arr_start] + new_data + hq[arr_end:]

    new_hq = re.sub(
        r'"xaxis":\{"title":\{"text":"Price[^"]*"\}',
        '"xaxis":{"title":{"text":"Distance from spot  →"},"ticksuffix":"%"',
        new_hq)

    zero_line = (
        '{"line":{"color":"rgba(31,42,51,0.25)","width":1,"dash":"dot"},'
        '"type":"line","x0":0,"x1":0,"xref":"x","y0":0,"y1":1,"yref":"y domain"}'
    )
    if '"shapes":[' in new_hq:
        new_hq = new_hq.replace('"shapes":[', '"shapes":[' + zero_line + ',', 1)
    else:
        new_hq = new_hq.replace(
            '"template":', '"shapes":[' + zero_line + '],"template":', 1)

    return html[:hq_seg.start()] + new_hq + html[hq_seg.end():]


# ── 6. COT Positioning Regime → Macro & Regime section ───────────────────────

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


# ── 7. Page navigation ────────────────────────────────────────────────────────

def add_index_nav(html):
    """Inject nav bar CSS and HTML into index.html (idempotent)."""
    css_needed = ''
    if COT_BLOCK_CSS not in html:
        css_needed += COT_BLOCK_CSS
    if NAV_CSS not in html:
        css_needed += NAV_CSS
    if css_needed:
        html = html.replace('</style>', css_needed + '</style>', 1)

    if 'class="page-nav"' not in html:
        html = re.sub(r'(</header>)', r'\1' + INDEX_NAV_HTML, html, count=1)
    return html


def add_cot_nav(html):
    """Inject nav bar CSS and HTML into COT_Analysis_Latest.html (idempotent)."""
    if 'class="page-nav"' in html:
        return html  # already present
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
        html = move_hq_and_renumber(html)
        print('✓ HQ section moved and sections renumbered', file=sys.stderr)

        html = hoist_plotly_to_head(html)
        print('✓ Plotly scripts hoisted into <head>', file=sys.stderr)

        html = sort_signal_matrix(html)
        print('✓ Signal Matrix sorted by conviction', file=sys.stderr)

        html = make_collapsible(html)
        html = inject_css(html)
        print('✓ Collapsible sections applied', file=sys.stderr)

        html = convert_hq_xaxis_to_pct(html)
        print('✓ HQ Buys x-axis converted to % from spot', file=sys.stderr)

        lookup = build_quality_lookup(html)
        print(f'✓ Quality lookup: {len(lookup)} tickers', file=sys.stderr)
        html = inject_quality_metrics(html, lookup)
        print('✓ ROIC/FCF injected into Conviction Map', file=sys.stderr)

    # COT Positioning Regime injection
    cot_html = None
    if cot_path and os.path.exists(cot_path):
        cot_html = open(cot_path, encoding='utf-8').read()
        cot_block = extract_cot_regime_block(cot_html)
        if cot_block:
            html = inject_cot_into_macro_regime(html, cot_block)
            print('✓ COT Positioning Regime injected into Macro & Regime',
                  file=sys.stderr)

    # Navigation bar
    html = add_index_nav(html)
    print('✓ Nav bar added to index.html', file=sys.stderr)

    open(path, 'w', encoding='utf-8').write(html)

    if cot_html is not None and cot_path:
        cot_html = add_cot_nav(cot_html)
        open(cot_path, 'w', encoding='utf-8').write(cot_html)
        print('✓ Nav bar added to COT_Analysis_Latest.html', file=sys.stderr)

    print('Done.', file=sys.stderr)


if __name__ == '__main__':
    main()
