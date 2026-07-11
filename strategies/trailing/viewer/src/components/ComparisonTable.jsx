import { fmtMoney } from "../lib/chartMath";

export default function ComparisonTable({ rows }) {
  const sorted = [...rows].sort((a, b) => b.roi - a.roi);
  return (
    <div className="panel">
      <h2>Comparación vanilla vs. trailing stop</h2>
      <table>
        <thead>
          <tr>
            <th>Estrategia</th>
            <th>ROI</th>
            <th>Ganancia</th>
            <th>Compras</th>
            <th>Ventas</th>
            <th>Trailing capture</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.key}>
              <th>{r.label}</th>
              <td>
                {r.roi >= 0 ? "+" : ""}
                {r.roi.toFixed(2)}%
              </td>
              <td>
                {r.profit >= 0 ? "+" : "-"}
                {fmtMoney(Math.abs(r.profit))}
              </td>
              <td>{r.buys}</td>
              <td>{r.sells}</td>
              <td>
                {r.trailingCapture == null
                  ? "—"
                  : `${r.trailingCapture >= 0 ? "+" : "-"}${fmtMoney(Math.abs(r.trailingCapture))}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
