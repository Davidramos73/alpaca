import argparse
import json


RISE_RAMP_LIGHT = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
                    "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
RISE_RAMP_DARK = ["#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
                   "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281"]

CAT_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7"]
CAT_DARK  = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9"]


def build_html(data: dict) -> str:
    symbol = data["symbol"]
    series = data["series"]
    price = data["price"]
    n = len(price)
    drop_values = sorted({s["drop_pct"] for s in series})
    by_drop = {d: sorted([s for s in series if s["drop_pct"] == d], key=lambda s: s["rise_pct"]) for d in drop_values}

    all_equity = [p["equity"] for s in series for p in s["points"]]
    y_min, y_max = min(all_equity), max(all_equity)
    pad = (y_max - y_min) * 0.05
    y_min -= pad
    y_max += pad

    price_vals = [p["close"] for p in price]
    p_min, p_max = min(price_vals), max(price_vals)
    p_pad = (p_max - p_min) * 0.05
    p_min -= p_pad
    p_max += p_pad

    dates = [p["date"] for p in price]
    date_ticks_idx = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1})

    # --- top 5 combinaciones por equity final, indexadas a 100 junto al precio ---
    top5 = sorted(series, key=lambda s: s["points"][-1]["equity"], reverse=True)[:5]

    def indexed(values):
        base = values[0]
        return [v / base * 100 for v in values]

    top5_indexed = [indexed([p["equity"] for p in s["points"]]) for s in top5]
    price_indexed = indexed(price_vals)
    all_indexed_vals = price_indexed + [v for series_vals in top5_indexed for v in series_vals]
    idx_min, idx_max = min(all_indexed_vals), max(all_indexed_vals)
    idx_pad = (idx_max - idx_min) * 0.05
    idx_min -= idx_pad
    idx_max += idx_pad

    def fmt_money(v):
        return f"${v/1000:,.0f}K" if abs(v) >= 1000 else f"${v:,.0f}"

    def facet_svg(drop_pct):
        w, h = 900, 220
        margin = {"top": 10, "right": 16, "bottom": 24, "left": 60}
        pw, ph = w - margin["left"] - margin["right"], h - margin["top"] - margin["bottom"]

        def x(i):
            return margin["left"] + (i / (n - 1)) * pw

        def y(v):
            return margin["top"] + (1 - (v - y_min) / (y_max - y_min)) * ph

        paths = []
        for s in by_drop[drop_pct]:
            pts = " ".join(f"{x(i):.1f},{y(p['equity']):.1f}" for i, p in enumerate(s["points"]))
            paths.append(
                f'<polyline class="eq-line" data-rise="{s["rise_pct"]}" points="{pts}" '
                f'style="stroke:var(--rise-{s["rise_pct"]})"/>'
            )

        y_ticks = [y_min + (y_max - y_min) * f for f in (0.0, 0.5, 1.0)]
        grid = "".join(
            f'<line class="grid" x1="{margin["left"]}" x2="{w-margin["right"]}" '
            f'y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text class="tick" x="{margin["left"]-6}" y="{y(v)+3:.1f}" text-anchor="end">{fmt_money(v)}</text>'
            for v in y_ticks
        )
        x_labels = "".join(
            f'<text class="tick x-tick" x="{x(i):.1f}" y="{h-6}" text-anchor="middle">{dates[i]}</text>'
            for i in date_ticks_idx
        )

        return f'''
        <div class="facet" data-drop="{drop_pct}">
          <div class="facet-title">drop {drop_pct}%</div>
          <svg viewBox="0 0 {w} {h}" style="aspect-ratio:{w}/{h}" data-margin-left="{margin["left"]}" data-margin-right="{margin["right"]}"
               data-plot-w="{pw}" data-n="{n}">
            <defs><clipPath id="facet-clip-{drop_pct}"><rect x="{margin["left"]}" y="0" width="{pw}" height="{h}"/></clipPath></defs>
            {grid}
            <g clip-path="url(#facet-clip-{drop_pct})">{"".join(paths)}</g>
            {x_labels}
            <line class="crosshair" x1="0" x2="0" y1="{margin["top"]}" y2="{h-margin["bottom"]}" style="display:none"/>
            <rect class="hitrect" x="{margin["left"]}" y="{margin["top"]}" width="{pw}" height="{ph}" fill="transparent"/>
          </svg>
        </div>'''

    facets_html = "".join(facet_svg(d) for d in drop_values)

    # --- top 5 vs. precio (indexado a 100 al inicio) ---
    t5_w, t5_h = 900, 260
    t5margin = {"top": 10, "right": 16, "bottom": 24, "left": 50}
    t5pw, t5ph = t5_w - t5margin["left"] - t5margin["right"], t5_h - t5margin["top"] - t5margin["bottom"]

    def t5x(i):
        return t5margin["left"] + (i / (n - 1)) * t5pw

    def t5y(v):
        return t5margin["top"] + (1 - (v - idx_min) / (idx_max - idx_min)) * t5ph

    t5_grid_ticks = [idx_min + (idx_max - idx_min) * f for f in (0.0, 0.5, 1.0)]
    t5_grid = "".join(
        f'<line class="grid" x1="{t5margin["left"]}" x2="{t5_w-t5margin["right"]}" '
        f'y1="{t5y(v):.1f}" y2="{t5y(v):.1f}"/>'
        f'<text class="tick" x="{t5margin["left"]-6}" y="{t5y(v)+3:.1f}" text-anchor="end">{v:.0f}</text>'
        for v in t5_grid_ticks
    )
    t5_x_labels = "".join(
        f'<text class="tick x-tick" x="{t5x(i):.1f}" y="{t5_h-6}" text-anchor="middle">{dates[i]}</text>'
        for i in date_ticks_idx
    )
    price_path = " ".join(f"{t5x(i):.1f},{t5y(v):.1f}" for i, v in enumerate(price_indexed))
    top5_paths = "".join(
        '<polyline class="t5-line" data-key="combo-{}" points="{}" style="stroke:var(--cat-{})"/>'.format(
            i + 1,
            " ".join(f"{t5x(j):.1f},{t5y(v):.1f}" for j, v in enumerate(vals)),
            i + 1,
        )
        for i, vals in enumerate(top5_indexed)
    )
    top5_legend = '<span class="legend-item legend-clickable" data-key="price" tabindex="0"><span class="swatch swatch-dashed"></span>Precio {} (referencia)</span>'.format(symbol)
    top5_legend += "".join(
        '<span class="legend-item legend-clickable" data-key="combo-{}" tabindex="0"><span class="swatch" style="background:var(--cat-{})"></span>drop {}% / rise {}% ({:+.1f}%)</span>'.format(
            i + 1, i + 1, s["drop_pct"], s["rise_pct"], (s["points"][-1]["equity"] / data["starting_cash"] - 1) * 100
        )
        for i, s in enumerate(top5)
    )

    # --- price panel ---
    pw_w, pw_h = 900, 220
    pmargin = {"top": 10, "right": 16, "bottom": 24, "left": 60}
    ppw, pph = pw_w - pmargin["left"] - pmargin["right"], pw_h - pmargin["top"] - pmargin["bottom"]

    def px(i):
        return pmargin["left"] + (i / (n - 1)) * ppw

    def py(v):
        return pmargin["top"] + (1 - (v - p_min) / (p_max - p_min)) * pph

    price_pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(price_vals))
    price_y_ticks = [p_min + (p_max - p_min) * f for f in (0.0, 0.5, 1.0)]
    price_grid = "".join(
        f'<line class="grid" x1="{pmargin["left"]}" x2="{pw_w-pmargin["right"]}" '
        f'y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
        f'<text class="tick" x="{pmargin["left"]-6}" y="{py(v)+3:.1f}" text-anchor="end">${v:,.0f}</text>'
        for v in price_y_ticks
    )
    price_x_labels = "".join(
        f'<text class="tick x-tick" x="{px(i):.1f}" y="{pw_h-6}" text-anchor="middle">{dates[i]}</text>'
        for i in date_ticks_idx
    )

    # --- precio + marcas de compra/venta (mejor combinación) ---
    best_combo = data.get("best_combo") or {}
    best_trades = data.get("best_trades") or []
    date_to_idx = {d: i for i, d in enumerate(dates)}

    trades_markers = []
    for t in best_trades:
        i = date_to_idx.get(t["date"])
        if i is None:
            continue
        cx, cy = px(i), py(t["price"])
        extra_attrs = f' data-order-id="{t.get("order_id", "")}"'
        if t["type"] == "BUY":
            pts = f"{cx:.1f},{cy-5:.1f} {cx-5:.1f},{cy+4:.1f} {cx+5:.1f},{cy+4:.1f}"
            color_var = "--marker-buy"
        else:
            pts = f"{cx:.1f},{cy+5:.1f} {cx-5:.1f},{cy-4:.1f} {cx+5:.1f},{cy-4:.1f}"
            color_var = "--marker-sell"
            extra_attrs += f' data-buy-price="{t["buy_price"]}" data-profit="{t["profit"]}" data-buy-date="{t.get("buy_date", "")}"'
        trades_markers.append(
            f'<g class="trade-marker" data-type="{t["type"]}" data-date="{t["date"]}" data-price="{t["price"]}"{extra_attrs}>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10" fill="transparent"/>'
            f'<polygon points="{pts}" style="fill:var({color_var})" stroke="var(--surface-1)" stroke-width="2"/>'
            f'</g>'
        )
    trades_markers_html = "".join(trades_markers)

    legend_swatches = "".join(
        f'<span class="legend-item"><span class="swatch" style="background:var(--rise-{r})"></span>{r}%</span>'
        for r in range(1, 11)
    )

    table_rows = "".join(
        "<tr><th>{}%</th>{}</tr>".format(
            d,
            "".join(
                f'<td>{fmt_money(next(s for s in by_drop[d] if s["rise_pct"] == r)["points"][-1]["equity"])}</td>'
                for r in range(1, 11)
            ),
        )
        for d in drop_values
    )
    table_header = "".join(f"<th>rise {r}%</th>" for r in range(1, 11))

    css_vars = ":root{" + ";".join(f"--rise-{i+1}:{c}" for i, c in enumerate(RISE_RAMP_LIGHT))
    css_vars += ";" + ";".join(f"--cat-{i+1}:{c}" for i, c in enumerate(CAT_LIGHT)) + "}"
    css_vars_dark = ":root{" + ";".join(f"--rise-{i+1}:{c}" for i, c in enumerate(RISE_RAMP_DARK))
    css_vars_dark += ";" + ";".join(f"--cat-{i+1}:{c}" for i, c in enumerate(CAT_DARK)) + "}"

    data_json = json.dumps(data).replace("</script", "<\\/script")

    return f'''<meta charset="utf-8">
<title>Equity por drop%/rise% — {symbol}</title>
<style>
  {css_vars}
  :root {{
    --surface-1: #fcfcfb; --page-plane: #f9f9f7; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9; --baseline: #c3c2b7;
    --price-line: #2a78d6; --marker-buy: #008300; --marker-sell: #e34948;
  }}
  @media (prefers-color-scheme: dark) {{
    {css_vars_dark}
    :root {{
      --surface-1: #1a1a19; --page-plane: #0d0d0d; --text-primary: #ffffff;
      --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a; --baseline: #383835;
      --price-line: #3987e5; --marker-buy: #008300; --marker-sell: #e66767;
    }}
  }}
  :root[data-theme="dark"] {{
    {css_vars_dark}
    --surface-1: #1a1a19; --page-plane: #0d0d0d; --text-primary: #ffffff;
    --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a; --baseline: #383835;
    --price-line: #3987e5; --marker-buy: #008300; --marker-sell: #e66767;
  }}
  :root[data-theme="light"] {{
    {css_vars}
    --surface-1: #fcfcfb; --page-plane: #f9f9f7; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9; --baseline: #c3c2b7;
    --price-line: #2a78d6; --marker-buy: #008300; --marker-sell: #e34948;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background: var(--page-plane); color: var(--text-primary);
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin-bottom: 20px; }}
  .panel {{ background: var(--surface-1); border-radius: 10px; padding: 16px; margin-bottom: 20px;
            overflow-x: auto; }}
  .panel h2 {{ font-size: 14px; margin: 0 0 10px; color: var(--text-secondary); font-weight: 600; }}
  svg {{ width: 100%; height: auto; display: block; overflow: visible; user-select: none; }}
  .grid {{ stroke: var(--gridline); stroke-width: 1; }}
  .tick {{ fill: var(--muted); font-size: 9px; }}
  .eq-line {{ fill: none; stroke-width: 2; }}
  .price-line {{ fill: none; stroke: var(--price-line); stroke-width: 2; }}
  .t5-line {{ fill: none; stroke-width: 2; transition: opacity 0.15s, stroke-width 0.15s; }}
  .t5-line.ref-line {{ stroke: var(--muted); stroke-width: 2; stroke-dasharray: 5 4; }}
  .t5-line.dimmed {{ opacity: 0.15; }}
  .t5-line.emphasized {{ stroke-width: 3; }}
  .crosshair {{ stroke: var(--baseline); stroke-width: 1; pointer-events: none; }}
  .hitrect {{ cursor: crosshair; }}
  .zoom-selection {{ fill: var(--price-line); opacity: 0.15; pointer-events: none; }}
  .facet-grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
  .facet {{ background: var(--surface-1); border-radius: 8px; padding: 8px 8px 4px; }}
  .facet-title {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 2px; font-weight: 600; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; font-size: 12px; color: var(--text-secondary); }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 4px; }}
  .legend-clickable {{ cursor: pointer; padding: 2px 6px; border-radius: 4px; user-select: none; }}
  .legend-clickable:hover {{ background: var(--gridline); }}
  .legend-clickable.active {{ background: var(--gridline); color: var(--text-primary); font-weight: 600; }}
  .swatch {{ width: 12px; height: 3px; border-radius: 2px; display: inline-block; }}
  .swatch-dashed {{ background: none !important; border-top: 2px dashed var(--muted); height: 0; }}
  .marker-swatch {{ width: 0; height: 0; display: inline-block; border-left: 6px solid transparent;
                     border-right: 6px solid transparent; }}
  .marker-swatch.buy {{ border-bottom: 9px solid var(--marker-buy); }}
  .marker-swatch.sell {{ border-top: 9px solid var(--marker-sell); }}
  .trade-marker {{ cursor: pointer; }}
  .trade-marker polygon {{ transition: transform 0.1s, stroke 0.1s, stroke-width 0.1s; transform-origin: center; transform-box: fill-box; }}
  .trade-marker:hover polygon {{ transform: scale(1.4); }}
  .trade-marker.linked polygon {{ stroke: var(--price-line); stroke-width: 3px; }}
  .tooltip {{ position: fixed; background: var(--surface-1); border: 1px solid var(--gridline);
              border-radius: 6px; padding: 8px 10px; font-size: 11px; pointer-events: none;
              box-shadow: 0 2px 8px rgba(0,0,0,0.15); z-index: 10; display: none; max-width: 260px; }}
  .tooltip .date {{ font-weight: 600; color: var(--text-primary); margin-bottom: 4px; }}
  .tooltip-row {{ display: flex; justify-content: space-between; gap: 10px; }}
  .tooltip-row .val {{ font-weight: 600; color: var(--text-primary); font-variant-numeric: tabular-nums; }}
  .tooltip-row .roi {{ font-weight: 400; color: var(--text-secondary); }}
  .tooltip-row .key {{ color: var(--text-secondary); }}
  .key-line {{ display: inline-block; width: 10px; height: 2px; margin-right: 4px; vertical-align: middle; }}
  button.toggle {{ background: var(--surface-1); border: 1px solid var(--gridline); color: var(--text-primary);
                   border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; margin-bottom: 10px; }}
  table {{ border-collapse: collapse; font-size: 12px; width: 100%; }}
  table th, table td {{ border: 1px solid var(--gridline); padding: 5px 8px; text-align: right;
                        font-variant-numeric: tabular-nums; }}
  table th {{ color: var(--text-secondary); font-weight: 600; }}
  table th:first-child, table td:first-child {{ text-align: left; }}
  #table-view {{ display: none; overflow-x: auto; }}
</style>

<h1>{symbol} — Equity por combinación drop% / rise%</h1>
<div class="subtitle">{data["date_start"]} → {data["date_end"]} · intervalo {data["interval_minutes"]} min · equity diaria al cierre de mercado</div>
<div class="subtitle">Tip: arrastrá para hacer zoom · scroll para acercar/alejar · doble click para volver a la vista completa</div>

<div class="panel">
  <h2>Precio {symbol}</h2>
  <svg viewBox="0 0 {pw_w} {pw_h}" style="aspect-ratio:{pw_w}/{pw_h}" id="price-svg" data-margin-left="{pmargin["left"]}" data-plot-w="{ppw}" data-n="{n}">
    <defs><clipPath id="price-clip"><rect x="{pmargin["left"]}" y="0" width="{ppw}" height="{pw_h}"/></clipPath></defs>
    {price_grid}
    <g clip-path="url(#price-clip)"><polyline class="price-line" points="{price_pts}"/></g>
    {price_x_labels}
    <line class="crosshair" x1="0" x2="0" y1="{pmargin["top"]}" y2="{pw_h-pmargin["bottom"]}" style="display:none"/>
    <rect class="hitrect" x="{pmargin["left"]}" y="{pmargin["top"]}" width="{ppw}" height="{pph}" fill="transparent"/>
  </svg>
</div>

<div class="panel">
  <h2>Precio {symbol} + compras/ventas (mejor combinación: drop {best_combo.get("drop_pct","?")}% / rise {best_combo.get("rise_pct","?")}%)</h2>
  <div class="legend">
    <span class="legend-item"><span class="marker-swatch buy"></span>Compra</span>
    <span class="legend-item"><span class="marker-swatch sell"></span>Venta</span>
  </div>
  <svg viewBox="0 0 {pw_w} {pw_h}" style="aspect-ratio:{pw_w}/{pw_h}" id="trades-svg" data-margin-left="{pmargin["left"]}" data-plot-w="{ppw}" data-n="{n}">
    <defs><clipPath id="trades-clip"><rect x="{pmargin["left"]}" y="0" width="{ppw}" height="{pw_h}"/></clipPath></defs>
    {price_grid}
    <g clip-path="url(#trades-clip)"><polyline class="price-line" points="{price_pts}"/></g>
    {price_x_labels}
    <line class="crosshair" x1="0" x2="0" y1="{pmargin["top"]}" y2="{pw_h-pmargin["bottom"]}" style="display:none"/>
    <rect class="hitrect" x="{pmargin["left"]}" y="{pmargin["top"]}" width="{ppw}" height="{pph}" fill="transparent"/>
    <g clip-path="url(#trades-clip)">{trades_markers_html}</g>
  </svg>
</div>

<div class="panel">
  <h2>Top 5 combinaciones vs. precio (indexado a 100 al inicio)</h2>
  <div class="legend" id="top5-legend">{top5_legend}</div>
  <svg viewBox="0 0 {t5_w} {t5_h}" style="aspect-ratio:{t5_w}/{t5_h}" id="top5-svg" data-margin-left="{t5margin["left"]}" data-plot-w="{t5pw}" data-n="{n}">
    <defs><clipPath id="top5-clip"><rect x="{t5margin["left"]}" y="0" width="{t5pw}" height="{t5_h}"/></clipPath></defs>
    {t5_grid}
    <g clip-path="url(#top5-clip)"><polyline class="t5-line ref-line" data-key="price" points="{price_path}"/>
    {top5_paths}</g>
    {t5_x_labels}
    <line class="crosshair" x1="0" x2="0" y1="{t5margin["top"]}" y2="{t5_h-t5margin["bottom"]}" style="display:none"/>
    <rect class="hitrect" x="{t5margin["left"]}" y="{t5margin["top"]}" width="{t5pw}" height="{t5ph}" fill="transparent"/>
  </svg>
</div>

<div class="panel">
  <h2>Equity por drop% (facetas) — color = rise%</h2>
  <div class="legend">{legend_swatches}</div>
  <button class="toggle" id="toggle-table">Ver tabla</button>
  <div id="table-view">
    <table>
      <thead><tr><th>drop \\ rise</th>{table_header}</tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>
  <div class="facet-grid" id="facet-grid">{facets_html}</div>
</div>

<div class="tooltip" id="tooltip"></div>

<script id="chart-data" type="application/json">{data_json}</script>
<script>
(function() {{
  const DATA = JSON.parse(document.getElementById('chart-data').textContent);
  const tooltip = document.getElementById('tooltip');
  const rampVar = i => getComputedStyle(document.documentElement).getPropertyValue('--rise-' + i).trim();

  document.getElementById('toggle-table').addEventListener('click', function() {{
    const t = document.getElementById('table-view');
    const showing = t.style.display === 'block';
    t.style.display = showing ? 'none' : 'block';
    this.textContent = showing ? 'Ver tabla' : 'Ver gráfico';
  }});

  function zoomDomain(svg) {{
    const n = parseInt(svg.dataset.n, 10);
    const d0 = svg.dataset.d0 ? parseFloat(svg.dataset.d0) : 0;
    const d1 = svg.dataset.d1 ? parseFloat(svg.dataset.d1) : n - 1;
    return [d0, d1];
  }}

  function nearestIndex(svg, clientX) {{
    const rect = svg.getBoundingClientRect();
    const marginLeft = parseFloat(svg.dataset.marginLeft);
    const plotW = parseFloat(svg.dataset.plotW);
    const n = parseInt(svg.dataset.n, 10);
    const vb = svg.viewBox.baseVal;
    const scaleX = rect.width / vb.width;
    const localX = vb.x + (clientX - rect.left) / scaleX;
    const d = zoomDomain(svg);
    const frac = (localX - marginLeft) / plotW;
    return Math.max(0, Math.min(n - 1, Math.round(d[0] + frac * (d[1] - d[0]))));
  }}

  function clientXToSvgX(svg, clientX) {{
    const rect = svg.getBoundingClientRect();
    const vb = svg.viewBox.baseVal;
    const scaleX = rect.width / vb.width;
    return vb.x + (clientX - rect.left) / scaleX;
  }}

  function svgX(svg, i) {{
    const marginLeft = parseFloat(svg.dataset.marginLeft);
    const plotW = parseFloat(svg.dataset.plotW);
    const d = zoomDomain(svg);
    return marginLeft + ((i - d[0]) / (d[1] - d[0])) * plotW;
  }}

  function showTooltip(clientX, clientY, html) {{
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    const pad = 14;
    let left = clientX + pad, top = clientY + pad;
    if (left + 260 > window.innerWidth) left = clientX - 260 - pad;
    if (top + 200 > window.innerHeight) top = clientY - 200 - pad;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }}

  function hideTooltip() {{ tooltip.style.display = 'none'; }}

  // --- price panel hover ---
  (function() {{
    const svg = document.getElementById('price-svg');
    const hit = svg.querySelector('.hitrect');
    const crosshair = svg.querySelector('.crosshair');
    hit.addEventListener('pointermove', function(e) {{
      if (svg.dataset.dragging === '1') return;
      const i = nearestIndex(svg, e.clientX);
      const x = svgX(svg, i);
      crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x);
      crosshair.style.display = 'block';
      const p = DATA.price[i];
      showTooltip(e.clientX, e.clientY,
        '<div class="date">' + p.date + '</div>' +
        '<div class="tooltip-row"><span class="key">Precio</span><span class="val">$' + p.close.toFixed(2) + '</span></div>');
    }});
    hit.addEventListener('pointerleave', function() {{ crosshair.style.display = 'none'; hideTooltip(); }});
  }})();

  // --- precio + trades hover (crosshair de precio) ---
  (function() {{
    const svg = document.getElementById('trades-svg');
    const hit = svg.querySelector('.hitrect');
    const crosshair = svg.querySelector('.crosshair');
    hit.addEventListener('pointermove', function(e) {{
      if (svg.dataset.dragging === '1') return;
      const i = nearestIndex(svg, e.clientX);
      const x = svgX(svg, i);
      crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x);
      crosshair.style.display = 'block';
      const p = DATA.price[i];
      showTooltip(e.clientX, e.clientY,
        '<div class="date">' + p.date + '</div>' +
        '<div class="tooltip-row"><span class="key">Precio</span><span class="val">$' + p.close.toFixed(2) + '</span></div>');
    }});
    hit.addEventListener('pointerleave', function() {{ crosshair.style.display = 'none'; hideTooltip(); }});
  }})();

  // --- top 5 vs. precio hover ---
  (function() {{
    const svg = document.getElementById('top5-svg');
    const hit = svg.querySelector('.hitrect');
    const crosshair = svg.querySelector('.crosshair');
    const top5 = DATA.series.slice().sort(function(a, b) {{
      return b.points[b.points.length - 1].equity - a.points[a.points.length - 1].equity;
    }}).slice(0, 5);

    hit.addEventListener('pointermove', function(e) {{
      if (svg.dataset.dragging === '1') return;
      const i = nearestIndex(svg, e.clientX);
      const x = svgX(svg, i);
      crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x);
      crosshair.style.display = 'block';

      const date = DATA.price[i].date;
      let html = '<div class="date">' + date + '</div>';
      html += '<div class="tooltip-row"><span class="key"><span class="key-line" style="background:var(--muted);border-top:2px dashed var(--muted)"></span>Precio</span>' +
        '<span class="val">$' + DATA.price[i].close.toFixed(2) + '</span></div>';
      top5.forEach(function(s, idx) {{
        const equity = s.points[i].equity;
        const roi = (equity - DATA.starting_cash) / DATA.starting_cash * 100;
        const roiStr = (roi >= 0 ? '+' : '') + roi.toFixed(2) + '%';
        html += '<div class="tooltip-row">' +
          '<span class="key"><span class="key-line" style="background:var(--cat-' + (idx+1) + ')"></span>drop ' + s.drop_pct + '%/rise ' + s.rise_pct + '%</span>' +
          '<span class="val">$' + Math.round(equity).toLocaleString() + ' <span class="roi">(' + roiStr + ')</span></span></div>';
      }});
      showTooltip(e.clientX, e.clientY, html);
    }});
    hit.addEventListener('pointerleave', function() {{ crosshair.style.display = 'none'; hideTooltip(); }});
  }})();

  // --- marcas de compra/venta: hover muestra fecha/tipo/precio + compra de origen y ganancia ---
  // Cada marca lleva su order_id real (asignado en optimize.py al abrir la posición; la venta
  // que la cierra carga el mismo id) — el emparejamiento compra<->venta NO se adivina por fecha
  // o cercanía visual, así que al pasar el mouse se resalta con un borde azul la marca exacta
  // que abrió/cerró esa operación, sin importar si hay otras marcas superpuestas encima.
  // Además, si varias marcas caen muy cerca en pantalla (mismo día, zoom insuficiente para
  // separarlas), se agrupan por cercanía real (getBoundingClientRect ya refleja el zoom aplicado)
  // y se listan todas juntas en el tooltip, así ninguna queda "tapada" detrás de otra.
  (function() {{
    const allMarkers = Array.prototype.slice.call(document.querySelectorAll('#trades-svg .trade-marker'));

    function markerCenter(g) {{
      const r = g.getBoundingClientRect();
      return {{ x: r.left + r.width / 2, y: r.top + r.height / 2 }};
    }}

    function nearbyMarkers(g) {{
      const c = markerCenter(g);
      return allMarkers.filter(function(other) {{
        const oc = markerCenter(other);
        return Math.hypot(c.x - oc.x, c.y - oc.y) <= 12;
      }}).sort(function(a, b) {{ return a.dataset.date.localeCompare(b.dataset.date); }});
    }}

    function linkedMarkers(g) {{
      if (!g.dataset.orderId) return [];
      return allMarkers.filter(function(other) {{ return other !== g && other.dataset.orderId === g.dataset.orderId; }});
    }}

    function tradeRowHtml(g) {{
      const type = g.dataset.type === 'BUY' ? 'Compra' : 'Venta';
      const colorVar = g.dataset.type === 'BUY' ? '--marker-buy' : '--marker-sell';
      const orderTag = g.dataset.orderId ? ' · orden #' + g.dataset.orderId : '';
      let html = '<div class="tooltip-row"><span class="key"><span class="key-line" style="background:var(' + colorVar + ')"></span>' +
        type + ' ' + g.dataset.date + orderTag + '</span><span class="val">$' + parseFloat(g.dataset.price).toFixed(2) + '</span></div>';
      if (g.dataset.type === 'SELL') {{
        const profit = parseFloat(g.dataset.profit);
        const sign = profit >= 0 ? '+' : '-';
        const profitColor = profit >= 0 ? 'var(--marker-buy)' : 'var(--marker-sell)';
        const buyPrice = parseFloat(g.dataset.buyPrice);
        const sellPrice = parseFloat(g.dataset.price);
        const priceDiff = sellPrice - buyPrice;
        const diffSign = priceDiff >= 0 ? '+' : '-';
        const diffColor = priceDiff >= 0 ? 'var(--marker-buy)' : 'var(--marker-sell)';
        const diffPct = buyPrice ? (priceDiff / buyPrice * 100) : 0;
        html += '<div class="tooltip-row"><span class="key">↳ abierta ' + (g.dataset.buyDate || '?') + '</span>' +
          '<span class="val">$' + buyPrice.toFixed(2) + '</span></div>';
        html += '<div class="tooltip-row"><span class="key">↳ venta − compra</span>' +
          '<span class="val" style="color:' + diffColor + '">' + diffSign + '$' + Math.abs(priceDiff).toFixed(2) +
          ' <span class="roi">(' + diffSign + Math.abs(diffPct).toFixed(2) + '%)</span></span></div>';
        html += '<div class="tooltip-row"><span class="key">↳ ganancia acumulada</span>' +
          '<span class="val" style="color:' + profitColor + '">' + sign + '$' + Math.abs(profit).toFixed(2) + '</span></div>';
      }}
      return html;
    }}

    allMarkers.forEach(function(g) {{
      g.addEventListener('pointerenter', function(e) {{
        linkedMarkers(g).forEach(function(m) {{ m.classList.add('linked'); }});
        const group = nearbyMarkers(g);
        let html = group.length > 1 ? '<div class="date">' + group.length + ' operaciones</div>' : '';
        html += group.map(tradeRowHtml).join('');
        showTooltip(e.clientX, e.clientY, html);
      }});
      g.addEventListener('pointermove', function(e) {{ showTooltip(e.clientX, e.clientY, tooltip.innerHTML); }});
      g.addEventListener('pointerleave', function() {{
        allMarkers.forEach(function(m) {{ m.classList.remove('linked'); }});
        hideTooltip();
      }});
    }});
  }})();

  // --- top 5 vs. precio: click en la leyenda resalta esa línea ---
  (function() {{
    const svg = document.getElementById('top5-svg');
    const lines = svg.querySelectorAll('.t5-line');
    const legendItems = document.querySelectorAll('#top5-legend .legend-clickable');
    let activeKey = null;

    function applyHighlight() {{
      lines.forEach(function(line) {{
        const isActive = activeKey === null || line.dataset.key === activeKey;
        line.classList.toggle('dimmed', !isActive);
        line.classList.toggle('emphasized', activeKey !== null && isActive);
      }});
      legendItems.forEach(function(item) {{
        item.classList.toggle('active', item.dataset.key === activeKey);
      }});
    }}

    function toggleKey(key) {{
      activeKey = (activeKey === key) ? null : key;
      applyHighlight();
    }}

    legendItems.forEach(function(item) {{
      item.addEventListener('click', function() {{ toggleKey(item.dataset.key); }});
      item.addEventListener('keydown', function(e) {{
        if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); toggleKey(item.dataset.key); }}
      }});
    }});
  }})();

  // --- facet hovers ---
  document.querySelectorAll('.facet').forEach(function(facetEl) {{
    const dropPct = parseInt(facetEl.dataset.drop, 10);
    const svg = facetEl.querySelector('svg');
    const hit = svg.querySelector('.hitrect');
    const crosshair = svg.querySelector('.crosshair');
    const seriesForDrop = DATA.series.filter(s => s.drop_pct === dropPct).sort((a,b) => a.rise_pct - b.rise_pct);

    hit.addEventListener('pointermove', function(e) {{
      if (svg.dataset.dragging === '1') return;
      const i = nearestIndex(svg, e.clientX);
      const x = svgX(svg, i);
      crosshair.setAttribute('x1', x); crosshair.setAttribute('x2', x);
      crosshair.style.display = 'block';

      const date = seriesForDrop[0].points[i].date;
      const rows = seriesForDrop.map(function(s) {{
        const equity = s.points[i].equity;
        const roi = (equity - DATA.starting_cash) / DATA.starting_cash * 100;
        return {{ rise: s.rise_pct, equity: equity, roi: roi }};
      }}).sort(function(a, b) {{ return b.equity - a.equity; }});

      let html = '<div class="date">drop ' + dropPct + '% · ' + date + '</div>';
      rows.forEach(function(r) {{
        const roiStr = (r.roi >= 0 ? '+' : '') + r.roi.toFixed(2) + '%';
        html += '<div class="tooltip-row">' +
          '<span class="key"><span class="key-line" style="background:' + rampVar(r.rise) + '"></span>rise ' + r.rise + '%</span>' +
          '<span class="val">$' + Math.round(r.equity).toLocaleString() + ' <span class="roi">(' + roiStr + ')</span></span></div>';
      }});
      showTooltip(e.clientX, e.clientY, html);
    }});
    hit.addEventListener('pointerleave', function() {{ crosshair.style.display = 'none'; hideTooltip(); }});
  }});

  // --- zoom: arrastrar selecciona un rango horizontal, doble click resetea ---
  // El viewBox nunca cambia: el zoom re-renderiza las posiciones X en espacio de
  // datos (índice de día), así las líneas/marcadores/textos no se deforman.
  function attachZoom(svg) {{
    const vbParts = svg.getAttribute('viewBox').split(' ').map(Number);
    const origVB = {{ x: vbParts[0], y: vbParts[1], w: vbParts[2], h: vbParts[3] }};
    const marginLeft = parseFloat(svg.dataset.marginLeft);
    const plotW = parseFloat(svg.dataset.plotW);
    const n = parseInt(svg.dataset.n, 10);
    const hit = svg.querySelector('.hitrect');
    if (!hit) return;

    const dates = DATA.price.map(function(p) {{ return p.date; }});
    const lines = Array.prototype.map.call(svg.querySelectorAll('polyline'), function(el) {{
      return {{
        el: el,
        ys: el.getAttribute('points').trim().split(/\\s+/).map(function(pair) {{
          return pair.split(',')[1];
        }})
      }};
    }});
    const markers = Array.prototype.map.call(svg.querySelectorAll('.trade-marker'), function(g) {{
      const cx = parseFloat(g.querySelector('circle').getAttribute('cx'));
      return {{ el: g, i: Math.round((cx - marginLeft) / plotW * (n - 1)), cx: cx }};
    }});
    const xLabels = Array.prototype.slice.call(svg.querySelectorAll('.x-tick'));

    function render() {{
      const d = zoomDomain(svg);
      const d0 = d[0], d1 = d[1];
      function xFor(i) {{ return marginLeft + ((i - d0) / (d1 - d0)) * plotW; }}
      lines.forEach(function(l) {{
        l.el.setAttribute('points', l.ys.map(function(y, i) {{
          return xFor(i).toFixed(1) + ',' + y;
        }}).join(' '));
      }});
      markers.forEach(function(m) {{
        m.el.setAttribute('transform', 'translate(' + (xFor(m.i) - m.cx).toFixed(1) + ' 0)');
        m.el.style.display = (m.i < d0 || m.i > d1) ? 'none' : '';
      }});
      const ticks = [];
      [0, 0.25, 0.5, 0.75, 1].forEach(function(f) {{
        const i = Math.round(d0 + (d1 - d0) * f);
        if (ticks.indexOf(i) === -1) ticks.push(i);
      }});
      xLabels.forEach(function(el, k) {{
        if (k < ticks.length) {{
          el.setAttribute('x', xFor(ticks[k]).toFixed(1));
          el.textContent = dates[ticks[k]];
          el.style.display = '';
        }} else {{
          el.style.display = 'none';
        }}
      }});
    }}

    const selection = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    selection.setAttribute('class', 'zoom-selection');
    selection.setAttribute('y', origVB.y);
    selection.setAttribute('height', origVB.h);
    selection.style.display = 'none';
    svg.appendChild(selection);

    let dragStartX = null;

    hit.addEventListener('pointerdown', function(e) {{
      dragStartX = clientXToSvgX(svg, e.clientX);
      hit.setPointerCapture(e.pointerId);
      svg.dataset.dragging = '1';
      hideTooltip();
      selection.setAttribute('x', dragStartX);
      selection.setAttribute('width', 0);
      selection.style.display = 'block';
    }});

    hit.addEventListener('pointermove', function(e) {{
      if (dragStartX === null) return;
      const curX = clientXToSvgX(svg, e.clientX);
      const x1 = Math.min(dragStartX, curX);
      const x2 = Math.max(dragStartX, curX);
      selection.setAttribute('x', x1);
      selection.setAttribute('width', x2 - x1);
    }});

    hit.addEventListener('pointerup', function(e) {{
      if (dragStartX === null) return;
      const curX = clientXToSvgX(svg, e.clientX);
      const x1 = Math.min(dragStartX, curX);
      const x2 = Math.max(dragStartX, curX);
      dragStartX = null;
      svg.dataset.dragging = '';
      selection.style.display = 'none';
      if (x2 - x1 <= 5) return;
      const d = zoomDomain(svg);
      function idxAt(x) {{ return d[0] + ((x - marginLeft) / plotW) * (d[1] - d[0]); }}
      const i1 = Math.max(0, Math.floor(idxAt(x1)));
      const i2 = Math.min(n - 1, Math.ceil(idxAt(x2)));
      if (i2 - i1 < 2) return;
      svg.dataset.d0 = i1;
      svg.dataset.d1 = i2;
      render();
    }});

    hit.addEventListener('pointercancel', function() {{
      dragStartX = null;
      svg.dataset.dragging = '';
      selection.style.display = 'none';
    }});

    svg.addEventListener('dblclick', function() {{
      svg.dataset.d0 = 0;
      svg.dataset.d1 = n - 1;
      render();
    }});

    // Rueda del mouse: aleja/acerca centrado en el cursor (scroll arriba = zoom in, abajo = zoom out).
    hit.addEventListener('wheel', function(e) {{
      e.preventDefault();
      const d = zoomDomain(svg);
      const range = d[1] - d[0];
      const factor = e.deltaY < 0 ? 0.8 : 1.25;
      let newRange = Math.max(2, Math.min(n - 1, range * factor));
      const localX = clientXToSvgX(svg, e.clientX);
      const frac = Math.max(0, Math.min(1, (localX - marginLeft) / plotW));
      const centerIdx = d[0] + frac * range;
      let newD0 = centerIdx - frac * newRange;
      let newD1 = newD0 + newRange;
      if (newD0 < 0) {{ newD1 -= newD0; newD0 = 0; }}
      if (newD1 > n - 1) {{ newD0 -= (newD1 - (n - 1)); newD1 = n - 1; }}
      newD0 = Math.max(0, newD0);
      svg.dataset.d0 = newD0;
      svg.dataset.d1 = newD1;
      render();
    }}, {{ passive: false }});
  }}

  document.querySelectorAll('svg[data-plot-w]').forEach(attachZoom);
}})();
</script>
'''


def main():
    parser = argparse.ArgumentParser(description="Genera un gráfico HTML interactivo a partir del JSON de equity de optimize.py")
    parser.add_argument("json_path", type=str, help="Ruta al JSON generado con optimize.py --export-equity-json")
    parser.add_argument("--out", type=str, default=None, help="Ruta del HTML de salida (default: mismo nombre con _chart.html)")
    args = parser.parse_args()

    with open(args.json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    out_path = args.out or args.json_path.rsplit(".", 1)[0].replace("_equity", "") + "_equity_chart.html"
    html = build_html(data)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML generado: {out_path}")


if __name__ == "__main__":
    main()
