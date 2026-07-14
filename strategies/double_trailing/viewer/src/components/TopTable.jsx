import { useMemo } from "react";
import { fmtMoney } from "../lib/chartMath";

function pct(v) {
  return v == null ? "—" : `${v}%`;
}

function money(v) {
  return v == null ? "—" : `${v >= 0 ? "+" : "-"}${fmtMoney(Math.abs(v))}`;
}

// Mapa de calor divergente: azul (ganancia) <-> gris (neutro) <-> rojo (pérdida),
// con la intensidad escalada según el máximo |ROI%| presente en la tabla.
function heatStyle(roi, maxAbs) {
  if (roi == null || maxAbs === 0) return undefined;
  const intensity = Math.min(Math.abs(roi) / maxAbs, 1);
  const mixPct = Math.round(intensity * 65);
  const hue = roi >= 0 ? "var(--price-line)" : "var(--marker-sell)";
  return { background: `color-mix(in srgb, ${hue} ${mixPct}%, var(--surface-1))` };
}

export default function TopTable({ rows, selectedKey, onSelect }) {
  const maxAbsRoi = useMemo(() => rows.reduce((max, r) => Math.max(max, Math.abs(r.roi)), 0), [rows]);

  return (
    <div className="panel">
      <h2>Top combinaciones vs. referencia vanilla</h2>
      <table>
        <thead>
          <tr>
            <th>Estrategia</th>
            <th>Drop</th>
            <th>Rise</th>
            <th>T.compra</th>
            <th>T.venta</th>
            <th>ROI</th>
            <th>Ganancia</th>
            <th>Compras</th>
            <th>Ventas</th>
            <th>Buy capture</th>
            <th>Sell capture</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.key}
              className={"row-clickable" + (selectedKey === r.key ? " active" : "")}
              onClick={() => onSelect(r.key)}
            >
              <th>{r.label}</th>
              <td>{pct(r.dropPct)}</td>
              <td>{pct(r.risePct)}</td>
              <td>{r.trailBuyPct == null ? "—" : `${r.trailBuyPct}%`}</td>
              <td>{r.trailSellPct == null ? "—" : `${r.trailSellPct}%`}</td>
              <td style={heatStyle(r.roi, maxAbsRoi)}>{r.roi >= 0 ? "+" : ""}{r.roi.toFixed(2)}%</td>
              <td>{money(r.profit)}</td>
              <td>{r.buys}</td>
              <td>{r.sells}</td>
              <td>{money(r.buyCapture)}</td>
              <td>{money(r.sellCapture)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
