"""
Post-processor for index.html (runs after each sync from DailyEquityScanner).

Transformations applied in order:
  1. Move "High-Quality Buys" from last to second named section; renumber.
  2. Hoist Plotly <script> tags into <head> so HQ Buys chart loads correctly.
  3. Sort Signal Matrix heatmap rows by blended conviction descending.
  4. Collapse Signal Matrix, What Changed Overnight, Master Signal Table,
     and Earnings Runway.
  5. Inject ROIC % · FCF % from Quality × Valuation into Conviction Map tooltips.
"""
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

COLLAPSE_TITLES = {'Signal Matrix', 'What Changed Overnight', 'Master Signal Table',
                   'Earnings Runway'}


# ── helpers ──────────────────────────────────────────────────────────────────

def _section_display_title(section_html):
    m = re.search(r'<span class="n">\d+</span>(.*?)</h2>', section_html)
    return m.group(1).strip() if m else ''


def _all_sections(html):
    return list(re.finditer(r'<section>.*?</section>', html, re.DOTALL))


# ── 0. hoist Plotly scripts into <head> ──────────────────────────────────────

def hoist_plotly_to_head(html):
    """Move PlotlyConfig + CDN <script> tags from body into <head>.

    When High-Quality Buys is section 02, its Plotly chart executes before
    the CDN script (which lives in the Conviction Map section further down).
    Hoisting both tags into <head> guarantees Plotly is available everywhere.
    """
    # Collect PlotlyConfig inline script
    config_pat = re.compile(r'<script>window\.PlotlyConfig\s*=.*?</script>')
    # Collect Plotly CDN script
    cdn_pat = re.compile(
        r'<script\s[^>]*src="https://cdn\.plot\.ly[^"]*"[^>]*></script>')

    config_tags = config_pat.findall(html)
    cdn_tags    = cdn_pat.findall(html)

    if not cdn_tags:
        return html  # nothing to hoist

    # Remove all occurrences from the body
    for tag in config_tags:
        html = html.replace(tag, '', 1)
    for tag in cdn_tags:
        html = html.replace(tag, '', 1)

    # Inject once into <head>, just before </head>
    inject = ''.join(config_tags[:1]) + ''.join(cdn_tags[:1])
    html = html.replace('</head>', inject + '</head>', 1)
    return html


# ── 1. move HQ Buys to slot 2 and renumber ───────────────────────────────────

def move_hq_and_renumber(html):
    segs = _all_sections(html)
    bodies = [s.group(0) for s in segs]

    # Section [0] is the unnumbered chips row — skip it for numbering purposes.
    # Named sections start at index 1.
    named = bodies[1:]

    hq_idx = next(
        (i for i, b in enumerate(named) if 'High-Quality' in _section_display_title(b)),
        None)
    if hq_idx is None:
        print('WARNING: High-Quality Buys section not found', file=sys.stderr)
        return html

    hq = named.pop(hq_idx)
    named.insert(1, hq)  # slot 2 (after Macro & Regime at index 0)

    # Renumber 01 … N
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
    """Parse [[a,b,...]] into list of lists of raw token strings (preserves null)."""
    return [[x.strip() for x in row.split(',')]
            for row in re.findall(r'\[([^\[\]]+)\]', s)]


def _row_mean(tokens):
    """Mean of a token row, treating null as 0."""
    vals = [0.0 if t == 'null' else float(t) for t in tokens]
    return sum(vals) / len(vals) if vals else 0.0


def _fmt_nested_floats(rows):
    """Format list-of-token-string rows back to JSON array."""
    return '[' + ','.join('[' + ','.join(r) + ']' for r in rows) + ']'


def _parse_nested_strings(s):
    """Parse [[\"a\",\"b\",...],[...]] into list of lists of strings."""
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

    order = sorted(range(len(y)),
                   key=lambda i: _row_mean(z[i]), reverse=True)
    y    = [y[i]    for i in order]
    z    = [z[i]    for i in order]
    text = [text[i] for i in order]

    # Rebuild the section with sorted arrays
    new_sm = sm
    new_sm = new_sm[:y_m.start()] + '"y":[' + ','.join(f'"{t}"' for t in y) + ']' + new_sm[y_m.end():]

    z_m2 = re.search(r'"z":(\[\[.*?\]\])', new_sm, re.DOTALL)
    new_sm = new_sm[:z_m2.start(1)] + _fmt_nested_floats(z) + new_sm[z_m2.end(1):]

    t_m2 = re.search(r'"text":(\[\[.*?\]\])', new_sm, re.DOTALL)
    new_sm = new_sm[:t_m2.start(1)] + _fmt_nested_strings(text) + new_sm[t_m2.end(1):]

    return html[:sm_seg.start()] + new_sm + html[sm_seg.end():]


# ── 3. collapse specified sections ───────────────────────────────────────────

def make_collapsible(html):
    def wrap(m):
        inner = m.group(1)
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
        patched, n = re.subn(
            r'(\\u003cbr\\u003eUpside.*?Technicals \w+)',
            lambda mo: mo.group(1) + BR + lookup[ticker],
            item, count=1)
        return f'"{patched}"' if n else m.group(0)

    new_cm = re.sub(r'"(\\u003cb\\u003e[^"]+)"', patch, cm.group(0))
    return html[:cm.start()] + new_cm + html[cm.end():]


# ── 5. HQ Buys chart: x-axis → % from spot ──────────────────────────────────

def _split_trace_objects(data_str):
    """Split a Plotly data array body into top-level {..} trace substrings."""
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
    """Convert HQ Buys chart x-axis from absolute price to % distance from spot.

    The chart has 5 traces per stock: the expected-move/gamma band (thick line),
    the spot diamond, put-wall, call-wall, and gamma-flip markers — all in raw
    dollar prices. We build a {ticker: spot} map from the diamond markers, then
    convert EVERY x value of EVERY trace using that stock's own spot price:
        pct = (price - spot) / spot * 100
    so spot sits at 0% and every stock shares one percentage scale regardless of
    absolute price level.
    """
    segs = _all_sections(html)
    hq_seg = next(
        (s for s in segs if 'High-Quality' in _section_display_title(s.group(0))),
        None)
    if not hq_seg:
        print('WARNING: High-Quality Buys section not found', file=sys.stderr)
        return html

    hq = hq_seg.group(0)

    # Locate the data array of the newPlot call (first balanced [ ... ]).
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

    # Pass 1 — build {ticker: spot} from diamond markers.
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

    # Pass 2 — convert every trace's x values using its own stock's spot.
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

        # Markers carry a "$%{x:.2f}" hover — x is now a percent, so stash the
        # original dollar price in customdata and show both $ and % from spot.
        if '"hovertemplate"' in trace and 'customdata' not in trace:
            cd = '"customdata":[' + ','.join(repr(v) for v in orig) + '],'
            trace = trace.replace('"hovertemplate":', cd + '"hovertemplate":', 1)
            trace = trace.replace(
                '$%{x:.2f}', '$%{customdata:.2f} (%{x:+.1f}% from spot)', 1)
        return trace

    new_traces = [convert(t) for t in traces]

    # Reassemble the data array, preserving the separators between traces.
    new_data = data[:spans[0][0]]
    for idx, (a, b) in enumerate(spans):
        new_data += new_traces[idx]
        sep_end = spans[idx + 1][0] if idx + 1 < len(spans) else len(data)
        new_data += data[b:sep_end]

    new_hq = hq[:arr_start] + new_data + hq[arr_end:]

    # Update x-axis: title + percent ticks.
    new_hq = re.sub(
        r'"xaxis":\{"title":\{"text":"Price[^"]*"\}',
        '"xaxis":{"title":{"text":"Distance from spot  →"},"ticksuffix":"%"',
        new_hq)

    # Add a dotted vertical reference line at x=0 (spot).
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


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'index.html'
    html = open(path, encoding='utf-8').read()

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

    open(path, 'w', encoding='utf-8').write(html)
    print('Done.', file=sys.stderr)


if __name__ == '__main__':
    main()
