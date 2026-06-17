"""
Post-processor for index.html.
Reads ROIC and FCF data from the "Quality x Valuation" section (03) and
injects it into every matching bubble tooltip in the "Conviction Map" (02).
"""
import re
import sys

BR   = '\\u003cbr\\u003e'  # <br> as JSON unicode escape
DOT  = '\\u00b7'           # · as JSON unicode escape

def build_lookup(html):
    """Return {ticker: 'ROIC X% · FCF X%'} from section 03."""
    s03 = re.search(
        r'<h2><span class="n">03</span>Quality.*?<h2><span class="n">04</span>',
        html, re.DOTALL)
    if not s03:
        print("WARNING: Quality x Valuation section not found", file=sys.stderr)
        return {}

    lookup = {}
    for block in re.findall(r'"hovertext":\[(.*?)\]', s03.group(0), re.DOTALL):
        for item in re.findall(r'"(\\u003cb\\u003e.*?)"', block):
            ticker_m = re.match(r'\\u003cb\\u003e(\w+)\\u003c\\u002fb\\u003e', item)
            if not ticker_m:
                continue
            ticker = ticker_m.group(1)

            parts = item.split('\\u003cbr\\u003e')
            # parts[1] = "EV/EBITDA Xth pct · ROIC X%"
            # parts[2] = "FCF X% · upside X%"
            if len(parts) < 3:
                continue

            roic_m = re.search(r'ROIC\s+(\S+)', parts[1])
            fcf_m  = re.search(r'^FCF\s+(\S+)', parts[2])
            if roic_m and fcf_m:
                lookup[ticker] = f'ROIC {roic_m.group(1)} {DOT} FCF {fcf_m.group(1)}'

    return lookup


def inject_into_conviction_map(html, lookup):
    """Inject ROIC/FCF line into each Conviction Map bubble tooltip."""
    if not lookup:
        return html

    s02_m = re.search(
        r'(<h2><span class="n">02</span>Conviction Map.*?)'
        r'(<h2><span class="n">03</span>)',
        html, re.DOTALL)
    if not s02_m:
        print("WARNING: Conviction Map section not found", file=sys.stderr)
        return html

    def patch_item(m):
        item = m.group(1)
        ticker_m = re.match(r'\\u003cb\\u003e(\w+)\\u003c', item)
        if not ticker_m or ticker_m.group(1) not in lookup:
            return m.group(0)

        ticker = ticker_m.group(1)
        extra = f'{BR}{lookup[ticker]}'

        # Insert after "Technicals WORD" (last word on that line)
        patched, n = re.subn(
            r'(\\u003cbr\\u003eUpside.*?Technicals \w+)',
            lambda mo: mo.group(1) + extra,
            item, count=1)
        if n == 0:
            return m.group(0)
        return f'"{patched}"'

    new_s02 = re.sub(r'"(\\u003cb\\u003e[^"]+)"', patch_item, s02_m.group(1))
    return html[:s02_m.start(1)] + new_s02 + html[s02_m.start(2):]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'index.html'
    html = open(path, encoding='utf-8').read()
    lookup = build_lookup(html)
    print(f"Quality lookup built: {len(lookup)} tickers", file=sys.stderr)
    html = inject_into_conviction_map(html, lookup)
    open(path, 'w', encoding='utf-8').write(html)
    print("Done.", file=sys.stderr)


if __name__ == '__main__':
    main()
