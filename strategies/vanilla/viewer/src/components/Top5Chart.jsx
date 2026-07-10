import { useMemo, useRef, useState } from "react";
import { useZoom } from "../hooks/useZoom";
import { xForIndex, nearestIndex, dateTicksForDomain, paddedDomain, indexed } from "../lib/chartMath";
import ChartFrame from "./ChartFrame";

const WIDTH = 900;
const HEIGHT = 260;
const MARGIN = { top: 10, right: 16, bottom: 24, left: 50 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;

const CAT_COUNT = 5;

export default function Top5Chart({ id, price, series, startingCash, showTooltip, hideTooltip }) {
  const svgRef = useRef(null);
  const n = price.length;
  const dates = useMemo(() => price.map((p) => p.date), [price]);
  const priceVals = useMemo(() => price.map((p) => p.close), [price]);
  const priceIndexed = useMemo(() => indexed(priceVals), [priceVals]);

  const top5 = useMemo(
    () =>
      series
        .slice()
        .sort((a, b) => b.points[b.points.length - 1].equity - a.points[a.points.length - 1].equity)
        .slice(0, CAT_COUNT),
    [series]
  );
  const top5Indexed = useMemo(() => top5.map((s) => indexed(s.points.map((p) => p.equity))), [top5]);

  const [yMin, yMax] = useMemo(() => {
    const all = [...priceIndexed, ...top5Indexed.flat()];
    return paddedDomain(all);
  }, [priceIndexed, top5Indexed]);

  const { domain, dragRect, handlers } = useZoom({ svgRef, n, marginLeft: MARGIN.left, plotWidth: PLOT_W });

  const x = (i) => xForIndex(i, { marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
  const y = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * (HEIGHT - MARGIN.top - MARGIN.bottom);

  const pricePath = useMemo(
    () => priceIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [priceIndexed, domain]
  );
  const top5Paths = useMemo(
    () => top5Indexed.map((vals) => vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")),
    [top5Indexed, domain]
  );

  const [activeKey, setActiveKey] = useState(null);
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
          <span className="key">
            <span className="key-line" style={{ background: "var(--muted)", borderTop: "2px dashed var(--muted)" }}></span>
            Precio
          </span>
          <span className="val">${priceVals[i].toFixed(2)}</span>
        </div>
        {top5.map((s, idx) => {
          const equity = s.points[i].equity;
          const roi = ((equity - startingCash) / startingCash) * 100;
          const roiStr = (roi >= 0 ? "+" : "") + roi.toFixed(2) + "%";
          return (
            <div className="tooltip-row" key={idx}>
              <span className="key">
                <span className="key-line" style={{ background: `var(--cat-${idx + 1})` }}></span>
                drop {s.drop_pct}%/rise {s.rise_pct}%
              </span>
              <span className="val">
                ${Math.round(equity).toLocaleString()} <span className="roi">({roiStr})</span>
              </span>
            </div>
          );
        })}
      </>
    );
  }

  function onHitPointerLeave() {
    setCrosshairX(null);
    hideTooltip();
  }

  function toggleKey(key) {
    setActiveKey((prev) => (prev === key ? null : key));
  }

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const dateTickIdx = dateTicksForDomain(domain, n);

  const gridContent = (
    <>
      {yTicks.map((v, i) => (
        <g key={i}>
          <line className="grid" x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(v)} y2={y(v)} />
          <text className="tick" x={MARGIN.left - 6} y={y(v) + 3} textAnchor="end">
            {v.toFixed(0)}
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
      <h2>Top 5 combinaciones vs. precio (indexado a 100 al inicio)</h2>
      <div className="legend">
        <span
          className={"legend-item legend-clickable" + (activeKey === "price" ? " active" : "")}
          tabIndex={0}
          onClick={() => toggleKey("price")}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), toggleKey("price"))}
        >
          <span className="swatch swatch-dashed"></span>Precio (referencia)
        </span>
        {top5.map((s, idx) => {
          const key = `combo-${idx + 1}`;
          const roi = ((s.points[s.points.length - 1].equity / startingCash - 1) * 100).toFixed(1);
          return (
            <span
              key={key}
              className={"legend-item legend-clickable" + (activeKey === key ? " active" : "")}
              tabIndex={0}
              onClick={() => toggleKey(key)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), toggleKey(key))}
            >
              <span className="swatch" style={{ background: `var(--cat-${idx + 1})` }}></span>
              drop {s.drop_pct}% / rise {s.rise_pct}% ({roi >= 0 ? "+" : ""}
              {roi}%)
            </span>
          );
        })}
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
        <polyline
          className={"t5-line ref-line" + (activeKey !== null && activeKey !== "price" ? " dimmed" : activeKey === "price" ? " emphasized" : "")}
          points={pricePath}
        />
        {top5Paths.map((pts, idx) => {
          const key = `combo-${idx + 1}`;
          const isActive = activeKey === null || activeKey === key;
          return (
            <polyline
              key={key}
              className={"t5-line" + (!isActive ? " dimmed" : activeKey !== null ? " emphasized" : "")}
              points={pts}
              style={{ stroke: `var(--cat-${idx + 1})` }}
            />
          );
        })}
      </ChartFrame>
    </div>
  );
}
