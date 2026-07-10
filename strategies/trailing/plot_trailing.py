#!/usr/bin/env python3
import argparse
import json

HTML_TEMPLATE = """<!DOCTYPE html>
<meta charset="utf-8">
<title>Trailing Stop — {symbol}</title>
<style>
  :root {{
    --surface-1: #fcfcfb; --page-plane: #f9f9f7; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9; --baseline: #c3c2b7;
    --price-line: #2a78d6; --equity-line: #1baf7a; --marker-buy: #008300; --marker-sell: #e34948;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface-1: #1a1a19; --page-plane: #0d0d0d; --text-primary: #ffffff;
      --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a; --baseline: #383835;
      --price-line: #3987e5; --equity-line: #199e70; --marker-buy: #008300; --marker-sell: #e66767;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background: var(--page-plane); color: var(--text-primary);
         font-family: system-ui, -apple-system, sans-serif; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin-bottom: 20px; }}
  .panel {{ background: var(--surface-1); border-radius: 10px; padding: 16px; margin-bottom: 20px; overflow-x: auto; }}
  .panel h2 {{ font-size: 14px; margin: 0 0 10px; color: var(--text-secondary); font-weight: 600; }}
  svg {{ width: 100%; height: auto; display: block; overflow: visible; user-select: none; }}
  .grid {{ stroke: var(--gridline); stroke-width: 1; }}
  .tick {{ fill: var(--muted); font-size: 9px; }}
  .price-line {{ fill: none; stroke: var(--price-line); stroke-width: 2; }}
  .equity-line {{ fill: none; stroke: var(--equity-line); stroke-width: 2; }}
  .crosshair {{ stroke: var(--baseline); stroke-width: 1; pointer-events: none; }}
  .hitrect {{ cursor: crosshair; }}
  .trade-marker {{ cursor: pointer; }}
  .trade-marker polygon {{ transition: transform 0.1s; transform-origin: center; transform-box: fill-box; }}
  .trade-marker:hover polygon {{ transform: scale(1.4); }}
  .tooltip {{ position: fixed; background: var(--surface-1); border: 1px solid var(--gridline);
              border-radius: 6px; padding: 8px 10px; font-size: 11px; pointer-events: none;
              box-shadow: 0 2px 8px rgba(0,0,0,0.15); z-index: 10; display: none; max-width: 300px; }}
  .tooltip .date {{ font-weight: 600; color: var(--text-primary); margin-bottom: 4px; }}
  .tooltip-row {{ display: flex; justify-content: space-between; gap: 10px; }}
  .tooltip-row .val {{ font-weight: 600; color: var(--text-primary); font-variant-numeric: tabular-nums; }}
  .tooltip-row .key {{ color: var(--text-secondary); }}
  .key-line {{ display: inline-block; width: 10px; height: 2px; margin-right: 4px; vertical-align: middle; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; font-size: 12px; color: var(--text-secondary); }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 4px; }}
  .swatch {{ width: 12px; height: 3px; border-radius: 2px; display: inline-block; }}
  .swatch-price {{ background: var(--price-line); }}
  .swatch-equity {{ background: var(--equity-line); }}
  .marker-swatch {{ width: 0; height: 0; display: inline-block; border-left: 6px solid transparent;
                     border-right: 6px solid transparent; }}
  .marker-swatch.buy {{ border-bottom: 9px solid var(--marker-buy); }}
  .marker-swatch.sell {{ border-top: 9px solid var(--marker-sell); }}
  table {{ border-collapse: collapse; font-size: 12px; width: 100%; max-width: 400px; margin-top: 10px; }}
  table td, table th {{ border: 1px solid var(--gridline); padding: 4px 8px; text-align: left; font-variant-numeric: tabular-nums; }}
  table th {{ color: var(--text-secondary); font-weight: 600; width: 40%; }}
  .zoom-selection {{ fill: var(--price-line); opacity: 0.15; pointer-events: none; }}
</style>

<h1>{symbol} — Trailing Stop {trail_display:.1f}%</h1>
<div class="subtitle">{date_start} → {date_end} · intervalo {interval_minutes} min · capital inicial ${starting_cash:,.0f}</div>

<div class="panel">
  <h2>Precio {symbol} y operaciones</h2>
  <div class="legend">
    <span class="legend-item"><span class="swatch swatch-price"></span>Precio</span>
    <span class="legend-item"><span class="marker-swatch buy"></span>Compra</span>
    <span class="legend-item"><span class="marker-swatch sell"></span>Venta</span>
  </div>
  <svg viewBox="0 0 900 250" id="price-svg" data-margin-left="60" data-plot-w="820" data-n="{n}">
    <defs><clipPath id="price-clip"><rect x="60" y="0" width="820" height="250"/></clipPath></defs>
    <g class="price-grid" id="price-grid"></g>
    <g clip-path="url(#price-clip)"><polyline class="price-line" points=""/></g>
    <g class="x-labels" id="price-x-labels"></g>
    <line class="crosshair" x1="0" x2="0" y1="10" y2="240" style="display:none"/>
    <rect class="hitrect" x="60" y="10" width="820" height="230" fill="transparent"/>
    <g clip-path="url(#price-clip)" class="trades-group" id="trades-group"></g>
  </svg>
</div>

<div class="panel">
  <h2>Equity y precio (indexados a 100 al inicio)</h2>
  <div class="legend">
    <span class="legend-item"><span class="swatch swatch-price"></span>Precio</span>
    <span class="legend-item"><span class="swatch swatch-equity"></span>Equity</span>
  </div>
  <svg viewBox="0 0 900 250" id="equity-svg" data-margin-left="50" data-plot-w="830" data-n="{n}">
    <defs><clipPath id="equity-clip"><rect x="50" y="0" width="830" height="250"/></clipPath></defs>
    <g class="grid" id="equity-grid"></g>
    <g clip-path="url(#equity-clip)">
      <polyline class="price-line" data-key="price" points=""/>
      <polyline class="equity-line" data-key="equity" points=""/>
    </g>
    <g class="x-labels" id="equity-x-labels"></g>
    <line class="crosshair" x1="0" x2="0" y1="10" y2="240" style="display:none"/>
    <rect class="hitrect" x="50" y="10" width="830" height="230" fill="transparent"/>
  </svg>
</div>

<div class="panel">
  <h2>Métricas de la simulación</h2>
  <table>
    <tr><th>ROI</th><td>{roi:+.2f}%</td></tr>
    <tr><th>Ganancia total</th><td>${profit:+,.0f}</td></tr>
    <tr><th>Capital final</th><td>${total_equity:,.0f}</td></tr>
    <tr><th>Total comisiones</th><td>${total_fees:,.0f}</td></tr>
    <tr><th>Compras</th><td>{buys}</td></tr>
    <tr><th>Ventas</th><td>{sells}</td></tr>
    <tr><th>Posiciones abiertas</th><td>{open_positions}</td></tr>
    <tr><th>Trailing capture total</th><td>${trailing_capture_total:+,.0f}</td></tr>
    <tr><th>Ventas por trailing</th><td>{trailing_sells}</td></tr>
  </table>
</div>

<div class="tooltip" id="tooltip"></div>

<script>
(function() {{
  const DATA = {data_json};
  const tooltip = document.getElementById('tooltip');
  const n = DATA.price.length;

  function buildPriceChart() {{
    const svg = document.getElementById('price-svg');
    const marginLeft = parseFloat(svg.dataset.marginLeft);
    const plotW = parseFloat(svg.dataset.plotW);
    const d0 = parseFloat(svg.dataset.d0 || 0);
    const d1 = parseFloat(svg.dataset.d1 || n - 1);
    const domain = d1 - d0;

    function x(i) {{ return marginLeft + ((i - d0) / domain) * plotW; }}
    const priceVals = DATA.price.map(p => p.close);
    const pMin = Math.min(...priceVals), pMax = Math.max(...priceVals);
    const pPad = (pMax - pMin) * 0.05;
    const pYmin = pMin - pPad, pYmax = pMax + pPad;

    function yPrice(v) {{ return 10 + (1 - (v - pYmin) / (pYmax - pYmin)) * 230; }}

    // Grid
    const grid = document.getElementById('price-grid');
    grid.innerHTML = '';
    for (let f of [0, 0.5, 1]) {{
      const v = pYmin + (pYmax - pYmin) * f;
      const yy = yPrice(v);
      grid.innerHTML += `<line class="grid" x1="${{marginLeft}}" x2="${{marginLeft+plotW}}" y1="${{yy}}" y2="${{yy}}"/>`;
      grid.innerHTML += `<text class="tick" x="${{marginLeft-6}}" y="${{yy+3}}" text-anchor="end">$${{v.toFixed(0)}}</text>`;
    }}

    // Price line
    const pts = priceVals.map((v, i) => x(i) + ',' + yPrice(v)).join(' ');
    svg.querySelector('.price-line').setAttribute('points', pts);

    // X labels
    const xLabels = document.getElementById('price-x-labels');
    xLabels.innerHTML = '';
    const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(d0 + domain * f));
    for (let i of ticks) {{
      if (i < 0 || i >= n) continue;
      xLabels.innerHTML += `<text class="tick x-tick" x="${{x(i)}}" y="244" text-anchor="middle">${{DATA.price[i].date}}</text>`;
    }}

    // Trades
    const tradesGroup = document.getElementById('trades-group');
    tradesGroup.innerHTML = '';
    const trades = DATA.trades || [];
    for (let t of trades) {{
      const isBuy = t.type === 'BUY' || t.type === 'BUY_INIT' || t.type === 'BUY_GRID';
      const isSell = t.type === 'SELL';
      if (!isBuy && !isSell) continue;
      const idx = DATA.price.findIndex(p => p.date === t.date);
      if (idx === -1) continue;
      const cx = x(idx);
      const cy = yPrice(t.price);
      const ptsPoly = isBuy
        ? `${{cx}},${{cy-5}} ${{cx-5}},${{cy+4}} ${{cx+5}},${{cy+4}}`
        : `${{cx}},${{cy+5}} ${{cx-5}},${{cy-4}} ${{cx+5}},${{cy-4}}`;
      const color = isBuy ? 'var(--marker-buy)' : 'var(--marker-sell)';
      const extra = isBuy ? '' : ` data-buy-price="${{t.buy_price}}" data-profit="${{t.profit}}" data-buy-date="${{t.buy_date}}"`;
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.setAttribute('class', 'trade-marker');
      g.setAttribute('data-type', t.type);
      g.setAttribute('data-date', t.date);
      g.setAttribute('data-price', t.price);
      if (t.order_id) g.setAttribute('data-order-id', t.order_id);
      g.innerHTML = `<circle cx="${{cx}}" cy="${{cy}}" r="10" fill="transparent"/>
                    <polygon points="${{ptsPoly}}" style="fill:${{color}}" stroke="var(--surface-1)" stroke-width="2"/>`;
      tradesGroup.appendChild(g);
    }}
  }}

  function buildEquityChart() {{
    const svg = document.getElementById('equity-svg');
    const marginLeft = parseFloat(svg.dataset.marginLeft);
    const plotW = parseFloat(svg.dataset.plotW);
    const d0 = parseFloat(svg.dataset.d0 || 0);
    const d1 = parseFloat(svg.dataset.d1 || n - 1);
    const domain = d1 - d0;

    function x(i) {{ return marginLeft + ((i - d0) / domain) * plotW; }}
    const priceVals = DATA.price.map(p => p.close);
    const equityVals = DATA.equity.map(e => e.equity);
    const basePrice = priceVals[0], baseEquity = equityVals[0];
    const idxPrice = priceVals.map(v => v / basePrice * 100);
    const idxEquity = equityVals.map(v => v / baseEquity * 100);
    const allVals = [...idxPrice, ...idxEquity];
    const minVal = Math.min(...allVals), maxVal = Math.max(...allVals);
    const pad = (maxVal - minVal) * 0.05;
    const yMin = minVal - pad, yMax = maxVal + pad;

    function y(v) {{ return 10 + (1 - (v - yMin) / (yMax - yMin)) * 230; }}

    // Grid
    const grid = document.getElementById('equity-grid');
    grid.innerHTML = '';
    for (let f of [0, 0.5, 1]) {{
      const v = yMin + (yMax - yMin) * f;
      const yy = y(v);
      grid.innerHTML += `<line class="grid" x1="${{marginLeft}}" x2="${{marginLeft+plotW}}" y1="${{yy}}" y2="${{yy}}"/>`;
      grid.innerHTML += `<text class="tick" x="${{marginLeft-6}}" y="${{yy+3}}" text-anchor="end">${{v.toFixed(0)}}</text>`;
    }}

    // Lines
    const pricePts = idxPrice.map((v, i) => x(i) + ',' + y(v)).join(' ');
    const equityPts = idxEquity.map((v, i) => x(i) + ',' + y(v)).join(' ');
    svg.querySelector('.price-line').setAttribute('points', pricePts);
    svg.querySelector('.equity-line').setAttribute('points', equityPts);

    // X labels
    const xLabels = document.getElementById('equity-x-labels');
    xLabels.innerHTML = '';
    const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(d0 + domain * f));
    for (let i of ticks) {{
      if (i < 0 || i >= n) continue;
      xLabels.innerHTML += `<text class="tick x-tick" x="${{x(i)}}" y="244" text-anchor="middle">${{DATA.price[i].date}}</text>`;
    }}
  }}

  // Zoom (drag & scroll) – se aplica a ambos svg
  function attachZoom(svgId) {{
    const svg = document.getElementById(svgId);
    const hit = svg.querySelector('.hitrect');
    if (!hit) return;
    const marginLeft = parseFloat(svg.dataset.marginLeft);
    const plotW = parseFloat(svg.dataset.plotW);
    const vb = svg.viewBox.baseVal;

    let dragging = false, startX = null, selection = null;
    selection = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    selection.setAttribute('class', 'zoom-selection');
    selection.setAttribute('y', vb.y);
    selection.setAttribute('height', vb.height);
    selection.style.display = 'none';
    svg.appendChild(selection);

    function getDomain() {{
      const d0 = parseFloat(svg.dataset.d0 || 0);
      const d1 = parseFloat(svg.dataset.d1 || n - 1);
      return [d0, d1];
    }}

    function setDomain(d0, d1) {{
      svg.dataset.d0 = Math.max(0, Math.min(n-1, d0));
      svg.dataset.d1 = Math.max(0, Math.min(n-1, d1));
      buildPriceChart();
      buildEquityChart();
    }}

    function clientXToSvgX(clientX) {{
      const rect = svg.getBoundingClientRect();
      const scaleX = rect.width / vb.width;
      return vb.x + (clientX - rect.left) / scaleX;
    }}

    function idxAtX(x) {{
      const [d0, d1] = getDomain();
      return d0 + ((x - marginLeft) / plotW) * (d1 - d0);
    }}

    hit.addEventListener('pointerdown', function(e) {{
      dragging = true;
      startX = clientXToSvgX(e.clientX);
      selection.setAttribute('x', startX);
      selection.setAttribute('width', 0);
      selection.style.display = 'block';
      hit.setPointerCapture(e.pointerId);
      svg.dataset.dragging = '1';
      tooltip.style.display = 'none';
    }});

    hit.addEventListener('pointermove', function(e) {{
      if (!dragging) return;
      const curX = clientXToSvgX(e.clientX);
      const x1 = Math.min(startX, curX);
      const x2 = Math.max(startX, curX);
      selection.setAttribute('x', x1);
      selection.setAttribute('width', x2 - x1);
    }});

    hit.addEventListener('pointerup', function(e) {{
      if (!dragging) return;
      const curX = clientXToSvgX(e.clientX);
      const x1 = Math.min(startX, curX);
      const x2 = Math.max(startX, curX);
      dragging = false;
      selection.style.display = 'none';
      svg.dataset.dragging = '';
      if (x2 - x1 <= 5) return;
      const i1 = Math.max(0, Math.floor(idxAtX(x1)));
      const i2 = Math.min(n-1, Math.ceil(idxAtX(x2)));
      if (i2 - i1 < 2) return;
      setDomain(i1, i2);
    }});

    hit.addEventListener('pointercancel', function() {{
      dragging = false;
      selection.style.display = 'none';
      svg.dataset.dragging = '';
    }});

    hit.addEventListener('wheel', function(e) {{
      e.preventDefault();
      const [d0, d1] = getDomain();
      const range = d1 - d0;
      const factor = e.deltaY < 0 ? 0.8 : 1.25;
      let newRange = Math.max(2, Math.min(n - 1, range * factor));
      const localX = clientXToSvgX(e.clientX);
      const frac = Math.max(0, Math.min(1, (localX - marginLeft) / plotW));
      const centerIdx = d0 + frac * range;
      let newD0 = centerIdx - frac * newRange;
      let newD1 = newD0 + newRange;
      if (newD0 < 0) {{ newD1 -= newD0; newD0 = 0; }}
      if (newD1 > n - 1) {{ newD0 -= (newD1 - (n - 1)); newD1 = n - 1; }}
      newD0 = Math.max(0, newD0);
      setDomain(newD0, newD1);
    }}, {{ passive: false }});

    svg.addEventListener('dblclick', function() {{
      setDomain(0, n - 1);
    }});
  }}

  // Tooltips en los gráficos
  function setupTooltips(svgId, getData) {{
    const svg = document.getElementById(svgId);
    const hit = svg.querySelector('.hitrect');
    const crosshair = svg.querySelector('.crosshair');
    if (!hit || !crosshair) return;

    hit.addEventListener('pointermove', function(e) {{
      if (svg.dataset.dragging === '1') return;
      const rect = svg.getBoundingClientRect();
      const vb = svg.viewBox.baseVal;
      const scaleX = rect.width / vb.width;
      const localX = vb.x + (e.clientX - rect.left) / scaleX;
      const [d0, d1] = [parseFloat(svg.dataset.d0 || 0), parseFloat(svg.dataset.d1 || n-1)];
      const marginLeft = parseFloat(svg.dataset.marginLeft);
      const plotW = parseFloat(svg.dataset.plotW);
      const idx = Math.round(d0 + ((localX - marginLeft) / plotW) * (d1 - d0));
      if (idx < 0 || idx >= n) return;
      const xPos = marginLeft + ((idx - d0) / (d1 - d0)) * plotW;
      crosshair.setAttribute('x1', xPos); crosshair.setAttribute('x2', xPos);
      crosshair.style.display = 'block';
      const info = getData(idx);
      if (info) {{
        tooltip.innerHTML = info;
        tooltip.style.display = 'block';
        let left = e.clientX + 14, top = e.clientY + 14;
        if (left + 260 > window.innerWidth) left = e.clientX - 260 - 14;
        if (top + 200 > window.innerHeight) top = e.clientY - 200 - 14;
        tooltip.style.left = left + 'px';
        tooltip.style.top = top + 'px';
      }}
    }});
    hit.addEventListener('pointerleave', function() {{
      crosshair.style.display = 'none';
      tooltip.style.display = 'none';
    }});
  }}

  // Tooltip para precio + trades
  setupTooltips('price-svg', function(idx) {{
    const p = DATA.price[idx];
    let html = `<div class="date">${{p.date}}</div>`;
    html += `<div class="tooltip-row"><span class="key">Precio</span><span class="val">$${{p.close.toFixed(2)}}</span></div>`;
    const trade = (DATA.trades || []).find(t => t.date === p.date);
    if (trade) {{
      const isBuy = trade.type === 'BUY' || trade.type === 'BUY_INIT' || trade.type === 'BUY_GRID';
      const typeLabel = isBuy ? 'Compra' : 'Venta';
      const color = isBuy ? 'var(--marker-buy)' : 'var(--marker-sell)';
      html += `<div class="tooltip-row"><span class="key"><span class="key-line" style="background:${{color}}"></span>${{typeLabel}}</span><span class="val">$${{trade.price.toFixed(2)}}</span></div>`;
      if (!isBuy && trade.type === 'SELL') {{
        html += `<div class="tooltip-row"><span class="key">↳ compra ${{trade.buy_date||'?'}}</span><span class="val">$${{trade.buy_price.toFixed(2)}}</span></div>`;
        const profit = parseFloat(trade.profit);
        const sign = profit >= 0 ? '+' : '-';
        const colorProfit = profit >= 0 ? 'var(--marker-buy)' : 'var(--marker-sell)';
        html += `<div class="tooltip-row"><span class="key">↳ ganancia</span><span class="val" style="color:${{colorProfit}}">${{sign}}$${{Math.abs(profit).toFixed(2)}}</span></div>`;
      }}
    }}
    return html;
  }});

  setupTooltips('equity-svg', function(idx) {{
    const p = DATA.price[idx];
    const eq = DATA.equity[idx];
    const eqIndexed = eq.equity / DATA.equity[0].equity * 100;
    const priceIndexed = p.close / DATA.price[0].close * 100;
    return `<div class="date">${{p.date}}</div>
            <div class="tooltip-row"><span class="key">Precio</span><span class="val">$${{p.close.toFixed(2)}} (${{priceIndexed.toFixed(1)}})</span></div>
            <div class="tooltip-row"><span class="key">Equity</span><span class="val">$${{eq.equity.toFixed(0)}} (${{eqIndexed.toFixed(1)}})</span></div>`;
  }});

  // Inicializar
  buildPriceChart();
  buildEquityChart();
  attachZoom('price-svg');
  attachZoom('equity-svg');
}})();
</script>
"""

def build_html(data):
    n = len(data["price"])
    trail_display = data.get("trail_pct", 0.0) * 100
    return HTML_TEMPLATE.format(
        symbol=data["symbol"],
        date_start=data["date_start"],
        date_end=data["date_end"],
        interval_minutes=data["interval_minutes"],
        starting_cash=data["starting_cash"],
        trail_display=trail_display,
        roi=data["roi"],
        profit=data["profit"],
        total_equity=data["total_equity"],
        total_fees=data["total_fees"],
        buys=data["buys"],
        sells=data["sells"],
        open_positions=data["open_positions"],
        trailing_capture_total=data.get("trailing_capture_total", 0.0),
        trailing_sells=data.get("trailing_sells", 0),
        n=n,
        data_json=json.dumps(data).replace("</script", "<\\/script")
    )

def main():
    parser = argparse.ArgumentParser(description="Genera gráfico HTML para JSON de trailing stop")
    parser.add_argument("json_path", help="Ruta al JSON generado con --trail-pcts")
    parser.add_argument("--out", default=None, help="Ruta de salida del HTML (default: nombre del JSON con .html)")
    args = parser.parse_args()

    with open(args.json_path, "r") as f:
        data = json.load(f)

    out = args.out or args.json_path.replace(".json", ".html")
    html = build_html(data)
    with open(out, "w") as f:
        f.write(html)
    print(f"HTML generado: {out}")

if __name__ == "__main__":
    main()