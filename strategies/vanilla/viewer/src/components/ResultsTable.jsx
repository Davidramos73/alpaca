import { useMemo } from "react";
import { fmtMoney } from "../lib/chartMath";

export default function ResultsTable({ series }) {
  const dropValues = useMemo(() => [...new Set(series.map((s) => s.drop_pct))].sort((a, b) => a - b), [series]);
  const riseValues = useMemo(() => [...Array(10)].map((_, i) => i + 1), []);

  const byDropRise = useMemo(() => {
    const map = new Map();
    for (const s of series) map.set(`${s.drop_pct}-${s.rise_pct}`, s);
    return map;
  }, [series]);

  return (
    <div className="panel">
      <h2>Resultado final por combinación drop% / rise%</h2>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>drop \ rise</th>
              {riseValues.map((r) => (
                <th key={r}>rise {r}%</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dropValues.map((d) => (
              <tr key={d}>
                <th>{d}%</th>
                {riseValues.map((r) => {
                  const s = byDropRise.get(`${d}-${r}`);
                  const finalEquity = s ? s.points[s.points.length - 1].equity : null;
                  return <td key={r}>{finalEquity != null ? fmtMoney(finalEquity) : "—"}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
