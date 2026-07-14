import { useMemo } from "react";

function fmtPct(value) {
  const decimals = Math.abs(value) < 0.01 && value !== 0 ? 4 : 2;
  return `${value >= 0 ? "+" : ""}${value.toFixed(decimals)}%`;
}

// Mapa de calor divergente: azul (ganancia) <-> gris (neutro) <-> rojo (pérdida),
// con la intensidad escalada según el máximo |%| presente en la tabla.
function heatStyle(pct, maxAbs) {
  if (pct == null || maxAbs === 0) return undefined;
  const intensity = Math.min(Math.abs(pct) / maxAbs, 1);
  const mixPct = Math.round(intensity * 65); // tope de saturación para no tapar el texto
  const hue = pct >= 0 ? "var(--price-line)" : "var(--marker-sell)";
  return { background: `color-mix(in srgb, ${hue} ${mixPct}%, var(--surface-1))` };
}

export default function ResultsTable({ series }) {
  const dropValues = useMemo(() => [...new Set(series.map((s) => s.drop_pct))].sort((a, b) => a - b), [series]);
  const riseValues = useMemo(() => [...Array(10)].map((_, i) => i + 1), []);

  const byDropRise = useMemo(() => {
    const map = new Map();
    for (const s of series) map.set(`${s.drop_pct}-${s.rise_pct}`, s);
    return map;
  }, [series]);

  const pctByDropRise = useMemo(() => {
    const map = new Map();
    for (const [key, s] of byDropRise) {
      const first = s.points[0]?.equity ?? null;
      const last = s.points[s.points.length - 1]?.equity ?? null;
      map.set(key, first ? ((last - first) / first) * 100 : null);
    }
    return map;
  }, [byDropRise]);

  const maxAbsPct = useMemo(() => {
    let max = 0;
    for (const pct of pctByDropRise.values()) {
      if (pct != null) max = Math.max(max, Math.abs(pct));
    }
    return max;
  }, [pctByDropRise]);

  return (
    <div className="panel">
      <h2>Retorno % por combinación drop% / rise%</h2>
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
                  const key = `${d}-${r}`;
                  const pct = pctByDropRise.get(key) ?? null;
                  return (
                    <td key={r} style={heatStyle(pct, maxAbsPct)}>
                      {pct != null ? fmtPct(pct) : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
