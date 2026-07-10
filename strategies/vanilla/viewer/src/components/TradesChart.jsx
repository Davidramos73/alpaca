import { useMemo, useRef, useState } from "react";
import { useZoom } from "../hooks/useZoom";
import { xForIndex, nearestIndex, dateTicksForDomain, paddedDomain, dayFraction } from "../lib/chartMath";
import ChartFrame from "./ChartFrame";

const WIDTH = 900;
const HEIGHT = 220;
const MARGIN = { top: 10, right: 16, bottom: 24, left: 60 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;

// Same-day markers get spread within [idx, idx + DAY_SLOT_WIDTH] by time of
// day, leaving a gap before the next day's point so they never cross over it.
const DAY_SLOT_WIDTH = 0.9;

export default function TradesChart({ id, title, price, trades, bestCombo, showTooltip, hideTooltip }) {
  const svgRef = useRef(null);
  const n = price.length;
  const dates = useMemo(() => price.map((p) => p.date), [price]);
  const values = useMemo(() => price.map((p) => p.close), [price]);
  const [yMin, yMax] = useMemo(() => paddedDomain(values), [values]);
  const [linkedOrderId, setLinkedOrderId] = useState(null);

  const { domain, dragRect, handlers } = useZoom({ svgRef, n, marginLeft: MARGIN.left, plotWidth: PLOT_W });

  const x = (i) => xForIndex(i, { marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
  const y = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * (HEIGHT - MARGIN.top - MARGIN.bottom);

  const points = useMemo(
    () => values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [values, domain]
  );

  const dateToIdx = useMemo(() => new Map(dates.map((d, i) => [d, i])), [dates]);

  const markers = useMemo(
    () =>
      trades
        .map((t) => ({ ...t, idx: dateToIdx.get(t.date) }))
        .filter((t) => t.idx !== undefined)
        .map((t) => ({ ...t, xIdx: t.idx + dayFraction(t.time) * DAY_SLOT_WIDTH })),
    [trades, dateToIdx]
  );

  const [crosshairX, setCrosshairX] = useState(null);

  function onHitPointerMove(e) {
    const i = nearestIndex(svgRef.current, e.clientX, { n, marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
    setCrosshairX(x(i));
    showTooltip(
      e.clientX,
      e.clientY,
      <>
        <div className="date">{dates[i]}</div>
        <div className="tooltip-row">
          <span className="key">Precio</span>
          <span className="val">${values[i].toFixed(2)}</span>
        </div>
      </>
    );
  }

  function onHitPointerLeave() {
    setCrosshairX(null);
    hideTooltip();
  }

  function markerCenter(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function onMarkerEnter(e, marker) {
    e.stopPropagation();
    setLinkedOrderId(marker.order_id ?? null);

    const allEls = Array.from(svgRef.current.querySelectorAll(".trade-marker"));
    const center = markerCenter(e.currentTarget);
    const nearby = allEls
      .map((el, k) => ({ el, m: markers[k], c: markerCenter(el) }))
      .filter(({ c }) => Math.hypot(c.x - center.x, c.y - center.y) <= 12)
      .map(({ m }) => m)
      .sort((a, b) => a.date.localeCompare(b.date));

    const group = nearby.length ? nearby : [marker];
    showTooltip(
      e.clientX,
      e.clientY,
      <>
        {group.length > 1 && <div className="date">{group.length} operaciones</div>}
        {group.map((m, k) => (
          <TradeRow key={k} trade={m} />
        ))}
      </>
    );
  }

  function onMarkerLeave() {
    setLinkedOrderId(null);
    hideTooltip();
  }

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const dateTickIdx = dateTicksForDomain(domain, n);

  const gridContent = (
    <>
      {yTicks.map((v, i) => (
        <g key={i}>
          <line className="grid" x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(v)} y2={y(v)} />
          <text className="tick" x={MARGIN.left - 6} y={y(v) + 3} textAnchor="end">
            ${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </text>
        </g>
      ))}
    </>
  );

  const xLabels = (
    <>
      {dateTickIdx.map((i) => (
        <text key={i} className="tick x-tick" x={x(i)} y={HEIGHT - 6} textAnchor="middle">
          {dates[i]}
        </text>
      ))}
    </>
  );

  return (
    <div className="panel">
      <h2>
        {title} (drop {bestCombo?.drop_pct ?? "?"}% / rise {bestCombo?.rise_pct ?? "?"}%)
      </h2>
      <div className="legend">
        <span className="legend-item">
          <span className="marker-swatch buy"></span>Compra
        </span>
        <span className="legend-item">
          <span className="marker-swatch sell"></span>Venta
        </span>
      </div>
      <ChartFrame
        id={id}
        svgRef={svgRef}
        width={WIDTH}
        height={HEIGHT}
        margin={MARGIN}
        dragRect={dragRect}
        zoomHandlers={handlers}
        onHitPointerMove={onHitPointerMove}
        onHitPointerLeave={onHitPointerLeave}
        crosshairX={crosshairX}
        gridContent={gridContent}
        xLabels={xLabels}
      >
        <polyline className="price-line" points={points} />
        {markers.map((m, k) => {
          if (m.idx < domain[0] || m.idx > domain[1]) return null;
          const cx = x(m.xIdx);
          const cy = y(m.price);
          const isLinked = m.order_id != null && m.order_id === linkedOrderId;
          const pts =
            m.type === "BUY"
              ? `${cx},${cy - 5} ${cx - 5},${cy + 4} ${cx + 5},${cy + 4}`
              : `${cx},${cy + 5} ${cx - 5},${cy - 4} ${cx + 5},${cy - 4}`;
          const colorVar = m.type === "BUY" ? "var(--marker-buy)" : "var(--marker-sell)";
          return (
            <g
              key={k}
              className={"trade-marker" + (isLinked ? " linked" : "")}
              onPointerEnter={(e) => onMarkerEnter(e, m)}
              onPointerMove={(e) => onMarkerEnter(e, m)}
              onPointerLeave={onMarkerLeave}
            >
              <circle cx={cx} cy={cy} r={10} fill="transparent" />
              <polygon points={pts} style={{ fill: colorVar }} stroke="var(--surface-1)" strokeWidth={2} />
            </g>
          );
        })}
      </ChartFrame>
    </div>
  );
}

function TradeRow({ trade }) {
  const type = trade.type === "BUY" ? "Compra" : "Venta";
  const colorVar = trade.type === "BUY" ? "var(--marker-buy)" : "var(--marker-sell)";
  const orderTag = trade.order_id ? ` · orden #${trade.order_id}` : "";
  const whenStr = trade.time ? `${trade.date} ${trade.time}` : trade.date;
  if (trade.type !== "SELL") {
    return (
      <div className="tooltip-row">
        <span className="key">
          <span className="key-line" style={{ background: colorVar }}></span>
          {type} {whenStr}
          {orderTag}
        </span>
        <span className="val">${trade.price.toFixed(2)}</span>
      </div>
    );
  }
  const profit = trade.profit;
  const sign = profit >= 0 ? "+" : "-";
  const profitColor = profit >= 0 ? "var(--marker-buy)" : "var(--marker-sell)";
  const priceDiff = trade.price - trade.buy_price;
  const diffSign = priceDiff >= 0 ? "+" : "-";
  const diffColor = priceDiff >= 0 ? "var(--marker-buy)" : "var(--marker-sell)";
  const diffPct = trade.buy_price ? (priceDiff / trade.buy_price) * 100 : 0;
  return (
    <>
      <div className="tooltip-row">
        <span className="key">
          <span className="key-line" style={{ background: colorVar }}></span>
          {type} {whenStr}
          {orderTag}
        </span>
        <span className="val">${trade.price.toFixed(2)}</span>
      </div>
      <div className="tooltip-row">
        <span className="key">
          ↳ abierta {trade.buy_date || "?"}
          {trade.buy_time ? ` ${trade.buy_time}` : ""}
        </span>
        <span className="val">${trade.buy_price.toFixed(2)}</span>
      </div>
      <div className="tooltip-row">
        <span className="key">↳ venta − compra</span>
        <span className="val" style={{ color: diffColor }}>
          {diffSign}${Math.abs(priceDiff).toFixed(2)} <span className="roi">({diffSign}{Math.abs(diffPct).toFixed(2)}%)</span>
        </span>
      </div>
      <div className="tooltip-row">
        <span className="key">↳ ganancia acumulada</span>
        <span className="val" style={{ color: profitColor }}>
          {sign}${Math.abs(profit).toFixed(2)}
        </span>
      </div>
    </>
  );
}
