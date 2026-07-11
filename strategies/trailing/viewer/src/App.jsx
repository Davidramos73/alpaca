import { useCallback, useEffect, useMemo, useState } from "react";
import ComparisonTable from "./components/ComparisonTable";
import TrailingTradesChart from "./components/TrailingTradesChart";
import IndexedEquityChart from "./components/IndexedEquityChart";
import RunForm from "./components/RunForm";
import Tooltip from "./components/Tooltip";
import { useTooltip } from "./hooks/useTooltip";

function runLabel(entry) {
  return `${entry.symbol} · ${entry.date_start} → ${entry.date_end} · corrida ${entry.run_ts}`;
}

function countTrades(trades, type) {
  return trades.filter((t) => t.type === type).length;
}

export default function App() {
  const [manifest, setManifest] = useState(null);
  const [manifestError, setManifestError] = useState(null);
  const [selectedRunKey, setSelectedRunKey] = useState(null);
  const [baseData, setBaseData] = useState(null);
  const [trailDatas, setTrailDatas] = useState(null); // [{trail_pct, data}]
  const [dataError, setDataError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [selectedSeriesKey, setSelectedSeriesKey] = useState(null); // "vanilla" | "trail-<pct>"
  const { tooltip, show, hide } = useTooltip();

  const fetchManifest = useCallback(async (selectNewest) => {
    try {
      const r = await fetch("/data/manifest.json", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const entries = await r.json();
      setManifest(entries);
      setManifestError(null);
      if (selectNewest) {
        if (entries.length > 0) setSelectedRunKey(`${entries[0].symbol}|${entries[0].run_ts}`);
        else {
          setSelectedRunKey(null);
          setBaseData(null);
          setTrailDatas(null);
        }
      }
    } catch (err) {
      setManifestError(err.message);
    }
  }, []);

  useEffect(() => {
    fetchManifest(true);
  }, [fetchManifest]);

  const selectedRun = useMemo(
    () => manifest?.find((r) => `${r.symbol}|${r.run_ts}` === selectedRunKey) ?? null,
    [manifest, selectedRunKey]
  );

  async function handleDelete() {
    if (!selectedRun) return;
    if (!window.confirm(`¿Borrar esta corrida?\n\n${runLabel(selectedRun)}`)) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await fetch("/api/delete-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_ts: selectedRun.run_ts, symbol: selectedRun.symbol }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      await fetchManifest(true);
    } catch (err) {
      setDeleteError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  useEffect(() => {
    if (!selectedRun) return;
    setBaseData(null);
    setTrailDatas(null);
    setDataError(null);
    setSelectedSeriesKey(null);

    Promise.all([
      fetch(`/data/${selectedRun.base_file}`, { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${selectedRun.base_file}`);
        return r.json();
      }),
      Promise.all(
        selectedRun.trail_files.map((t) =>
          fetch(`/data/${t.file}`, { cache: "no-store" })
            .then((r) => {
              if (!r.ok) throw new Error(`HTTP ${r.status} en ${t.file}`);
              return r.json();
            })
            .then((data) => ({ trail_pct: t.trail_pct, data }))
        )
      ),
    ])
      .then(([base, trails]) => {
        setBaseData(base);
        setTrailDatas(trails);
      })
      .catch((err) => setDataError(err.message));
  }, [selectedRun]);

  const vanillaSeriesForEquity = useMemo(() => {
    if (!baseData) return null;
    return baseData.series.find(
      (s) => s.drop_pct === baseData.best_combo.drop_pct && s.rise_pct === baseData.best_combo.rise_pct
    );
  }, [baseData]);

  const rows = useMemo(() => {
    if (!baseData || !trailDatas || !vanillaSeriesForEquity) return [];
    const finalEquity = vanillaSeriesForEquity.points[vanillaSeriesForEquity.points.length - 1].equity;
    const profit = finalEquity - baseData.starting_cash;
    const roi = (profit / baseData.starting_cash) * 100;
    const vanillaRow = {
      key: "vanilla",
      label: `Vanilla (drop ${baseData.best_combo.drop_pct}% / rise ${baseData.best_combo.rise_pct}%)`,
      roi,
      profit,
      buys: countTrades(baseData.best_trades, "BUY"),
      sells: countTrades(baseData.best_trades, "SELL"),
      trailingCapture: null,
    };
    const trailRows = trailDatas.map(({ trail_pct, data }) => ({
      key: `trail-${trail_pct}`,
      label: `Trailing ${trail_pct.toFixed(1)}%`,
      roi: data.roi,
      profit: data.profit,
      buys: data.buys,
      sells: data.sells,
      trailingCapture: data.trailing_capture_total,
    }));
    return [vanillaRow, ...trailRows];
  }, [baseData, trailDatas, vanillaSeriesForEquity]);

  const bestTrailKey = useMemo(() => {
    const trailRows = rows.filter((r) => r.key !== "vanilla");
    if (trailRows.length === 0) return null;
    return trailRows.reduce((best, r) => (r.roi > best.roi ? r : best), trailRows[0]).key;
  }, [rows]);

  const activeSeriesKey = selectedSeriesKey ?? bestTrailKey ?? "vanilla";
  const activeTrail = useMemo(
    () => trailDatas?.find((t) => `trail-${t.trail_pct}` === activeSeriesKey) ?? null,
    [trailDatas, activeSeriesKey]
  );

  return (
    <div className="app">
      <div className="app-header">
        <h1>Trailing Stop Viewer</h1>
        {manifest && manifest.length > 0 && (
          <div className="run-picker">
            <select value={selectedRunKey ?? ""} onChange={(e) => setSelectedRunKey(e.target.value)}>
              {manifest.map((entry) => (
                <option key={`${entry.symbol}|${entry.run_ts}`} value={`${entry.symbol}|${entry.run_ts}`}>
                  {runLabel(entry)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="delete-run-btn"
              onClick={handleDelete}
              disabled={deleting || !selectedRun}
              title="Borrar esta corrida"
            >
              {deleting ? "Borrando…" : "Borrar"}
            </button>
          </div>
        )}
      </div>

      {deleteError && <div className="panel error">No se pudo borrar la corrida: {deleteError}</div>}

      <RunForm onRunComplete={() => fetchManifest(true)} />

      {manifestError && (
        <div className="panel error">
          No se pudo cargar data/manifest.json ({manifestError}). Corré optimize.py con --export-equity-json
          --trail-pcts apuntando a esta carpeta.
        </div>
      )}
      {manifest && manifest.length === 0 && (
        <div className="panel error">No hay corridas en data/. Generá una desde el form de arriba.</div>
      )}
      {dataError && <div className="panel error">No se pudo cargar la corrida: {dataError}</div>}

      {baseData && trailDatas && rows.length > 0 && (
        <>
          <div className="subtitle">
            {baseData.date_start} → {baseData.date_end} · intervalo 1 min (fijo) · equity diaria al cierre de
            mercado
          </div>

          <ComparisonTable rows={rows} />

          <div className="legend">
            {rows.map((r) => (
              <span
                key={r.key}
                className={"legend-item legend-clickable" + (activeSeriesKey === r.key ? " active" : "")}
                tabIndex={0}
                onClick={() => setSelectedSeriesKey(r.key)}
                onKeyDown={(e) =>
                  (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setSelectedSeriesKey(r.key))
                }
              >
                {r.label}
              </span>
            ))}
          </div>

          {activeSeriesKey === "vanilla" ? (
            <TrailingTradesChart
              id="trades"
              title={`Precio ${baseData.symbol} + operaciones — Vanilla`}
              price={baseData.price}
              trades={baseData.best_trades}
              showTooltip={show}
              hideTooltip={hide}
            />
          ) : (
            activeTrail && (
              <TrailingTradesChart
                id="trades"
                title={`Precio ${baseData.symbol} + operaciones — Trailing ${activeTrail.trail_pct.toFixed(1)}%`}
                price={activeTrail.data.price}
                trades={activeTrail.data.trades}
                showTooltip={show}
                hideTooltip={hide}
              />
            )
          )}

          {activeSeriesKey !== "vanilla" && activeTrail ? (
            <IndexedEquityChart
              id="equity-compare"
              price={baseData.price}
              vanillaEquity={vanillaSeriesForEquity.points}
              trailingEquity={activeTrail.data.equity}
              trailingLabel={`Trailing ${activeTrail.trail_pct.toFixed(1)}%`}
              showTooltip={show}
              hideTooltip={hide}
            />
          ) : (
            <div className="panel">
              <p className="subtitle" style={{ margin: 0 }}>
                Elegí un % de trailing arriba para comparar su curva de equity contra vanilla.
              </p>
            </div>
          )}
        </>
      )}

      <Tooltip tooltip={tooltip} />
    </div>
  );
}
