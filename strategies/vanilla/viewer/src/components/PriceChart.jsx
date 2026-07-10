import { useMemo, useRef, useState } from "react";
import { useZoom } from "../hooks/useZoom";
import { xForIndex, nearestIndex, dateTicksForDomain, paddedDomain } from "../lib/chartMath";
import ChartFrame from "./ChartFrame";

const WIDTH = 900;
const HEIGHT = 220;
const MARGIN = { top: 10, right: 16, bottom: 24, left: 60 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;

export default function PriceChart({ id, title, price, showTooltip, hideTooltip }) {
  const svgRef = useRef(null);
  const n = price.length;
  const dates = useMemo(() => price.map((p) => p.date), [price]);
  const values = useMemo(() => price.map((p) => p.close), [price]);
  const [yMin, yMax] = useMemo(() => paddedDomain(values), [values]);

  const { domain, dragRect, handlers } = useZoom({ svgRef, n, marginLeft: MARGIN.left, plotWidth: PLOT_W });

  const x = (i) => xForIndex(i, { marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
  const y = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * (HEIGHT - MARGIN.top - MARGIN.bottom);

  const points = useMemo(
    () => values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [values, domain]
  );

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const dateTickIdx = dateTicksForDomain(domain, n);

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
      <h2>{title}</h2>
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
      </ChartFrame>
    </div>
  );
}
