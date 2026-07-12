import { useMemo, useRef, useState } from "react";
import { useZoom } from "../hooks/useZoom";
import { xForIndex, nearestIndex, dateTicksForDomain, paddedDomain, indexed } from "../lib/chartMath";
import ChartFrame from "./ChartFrame";

const WIDTH = 900;
const HEIGHT = 260;
const MARGIN = { top: 10, right: 16, bottom: 24, left: 50 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;

// Forward-fills equity points onto `dates` by date, so vanillaEquity/
// trailingEquity don't need to share exact dates/length with price — a
// missing date just carries the last known value forward.
function alignByDate(dates, points) {
  const byDate = new Map(points.map((p) => [p.date, p.equity]));
  let last = points.length ? points[0].equity : 0;
  return dates.map((d) => {
    if (byDate.has(d)) last = byDate.get(d);
    return last;
  });
}

export default function IndexedEquityChart({
  id,
  price,
  vanillaEquity,
  trailingEquity,
  trailingLabel,
  showTooltip,
  hideTooltip,
}) {
  const svgRef = useRef(null);
  const n = price.length;
  const dates = useMemo(() => price.map((p) => p.date), [price]);
  const priceVals = useMemo(() => price.map((p) => p.close), [price]);
  const priceIndexed = useMemo(() => indexed(priceVals), [priceVals]);

  const vanillaVals = useMemo(() => alignByDate(dates, vanillaEquity), [dates, vanillaEquity]);
  const trailingVals = useMemo(() => alignByDate(dates, trailingEquity), [dates, trailingEquity]);
  const vanillaIndexed = useMemo(() => indexed(vanillaVals), [vanillaVals]);
  const trailingIndexed = useMemo(() => indexed(trailingVals), [trailingVals]);

  const [yMin, yMax] = useMemo(
    () => paddedDomain([...priceIndexed, ...vanillaIndexed, ...trailingIndexed]),
    [priceIndexed, vanillaIndexed, trailingIndexed]
  );

  const { domain, dragRect, handlers } = useZoom({ svgRef, n, marginLeft: MARGIN.left, plotWidth: PLOT_W });

  const x = (i) => xForIndex(i, { marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
  const y = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * (HEIGHT - MARGIN.top - MARGIN.bottom);

  const pricePath = useMemo(
    () => priceIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [priceIndexed, domain]
  );
  const vanillaPath = useMemo(
    () => vanillaIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [vanillaIndexed, domain]
  );
  const trailingPath = useMemo(
    () => trailingIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [trailingIndexed, domain]
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
          <span className="key">
            <span
              className="key-line"
              style={{ background: "var(--muted)", borderTop: "2px dashed var(--muted)" }}
            ></span>
            Precio
          </span>
          <span className="val">${priceVals[i].toFixed(2)}</span>
        </div>
        <div className="tooltip-row">
          <span className="key">
            <span className="key-line" style={{ background: "var(--price-line)" }}></span>
            Vanilla
          </span>
          <span className="val">{vanillaIndexed[i].toFixed(1)}</span>
        </div>
        <div className="tooltip-row">
          <span className="key">
            <span className="key-line" style={{ background: "var(--equity-line)" }}></span>
            {trailingLabel}
          </span>
          <span className="val">{trailingIndexed[i].toFixed(1)}</span>
        </div>
      </>
    );
  }

  function onHitPointerLeave() {
    setCrosshairX(null);
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
      <h2>
        Equity vanilla vs. {trailingLabel} (indexado a 100 al inicio)
      </h2>
      <div className="legend">
        <span className="legend-item">
          <span className="swatch swatch-dashed"></span>Precio (referencia)
        </span>
        <span className="legend-item">
          <span className="swatch" style={{ background: "var(--price-line)" }}></span>Vanilla
        </span>
        <span className="legend-item">
          <span className="swatch" style={{ background: "var(--equity-line)" }}></span>
          {trailingLabel}
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
        <polyline className="idx-line ref-line" points={pricePath} />
        <polyline className="idx-line vanilla" points={vanillaPath} />
        <polyline className="idx-line trailing" points={trailingPath} />
      </ChartFrame>
    </div>
  );
}
