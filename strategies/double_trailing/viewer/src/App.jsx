import { useCallback, useEffect, useMemo, useState } from "react";
import TopTable from "./components/TopTable";
import TrailingTradesChart from "./components/TrailingTradesChart";
import IndexedEquityChart from "./components/IndexedEquityChart";
import ExposureChart from "./components/ExposureChart";
import RunForm from "./components/RunForm";
import Tooltip from "./components/Tooltip";
import { useTooltip } from "./hooks/useTooltip";

function runLabel(entry) {
  return `${entry.symbol} · ${entry.date_start} → ${entry.date_end} · corrida ${entry.run_ts}`;
}

function comboKey(c, i) {
  return `combo-${i}-${c.drop_pct}-${c.rise_pct}-${c.trail_buy_pct}-${c.trail_sell_pct}`;
}

export default function App() {
  const [manifest, setManifest] = useState(null);
  const [manifestError, setManifestError] = useState(null);
  const [selectedRunKey, setSelectedRunKey] = useState(null);
  const [runData, setRunData] = useState(null);
  const [dataError, setDataError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [selectedRowKey, setSelectedRowKey] = useState(null);
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
          setRunData(null);
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

  useEffect(() => {
    if (!selectedRun) return;
    setRunData(null);
    setDataError(null);
    setSelectedRowKey(null);
    fetch(`/data/${selectedRun.file}`, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${selectedRun.file}`);
        return r.json();
      })
      .then(setRunData)
      .catch((err) => setDataError(err.message));
  }, [selectedRun]);

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

  const rows = useMemo(() => {
    if (!runData) return [];
    const v = runData.vanilla;
    const vanillaRow = {
      key: "vanilla",
      label: `Vanilla (drop ${v.drop_pct}% / rise ${v.rise_pct}%)`,
      dropPct: v.drop_pct,
      risePct: v.rise_pct,
      trailBuyPct: null,
      trailSellPct: null,
      roi: v.roi,
      profit: v.profit,
      buys: v.buys,
      sells: v.sells,
      buyCapture: null,
      sellCapture: null,
    };
    const comboRows = runData.combos.map((c, i) => ({
      key: comboKey(c, i),
      label: `#${i + 1}`,
      dropPct: c.drop_pct,
      risePct: c.rise_pct,
      trailBuyPct: c.trail_buy_pct,
      trailSellPct: c.trail_sell_pct,
      roi: c.roi,
      profit: c.profit,
      buys: c.buys,
      sells: c.sells,
      buyCapture: c.buy_capture_total,
      sellCapture: c.trailing_capture_total,
    }));
    return [...comboRows, vanillaRow].sort((a, b) => b.roi - a.roi);
  }, [runData]);

  const activeRowKey = selectedRowKey ?? rows[0]?.key ?? null;
  const activeCombo = useMemo(() => {
    if (!runData || activeRowKey == null || activeRowKey === "vanilla") return null;
    const i = runData.combos.findIndex((c, idx) => comboKey(c, idx) === activeRowKey);
    return i >= 0 ? runData.combos[i] : null;
  }, [runData, activeRowKey]);

  return (
    <div className="app">
      <div className="app-header">
        <h1>Double Trailing Viewer</h1>
        {manifest && manifest.length > 0 && (
          <div className="run-picker">
            <select value={selectedRunKey ?? ""} onChange={(e) => setSelectedRunKey(e.target.value)}>
              {manifest.map((entry) => (
                <option key={`${entry.symbol}|${entry.run_ts}`} value={`${entry.symbol}|${entry.run_ts}`}>
                  {runLabel(entry)}
                </option>
              ))}
            </select>
            <button type="button" className="delete-run-btn" onClick={handleDelete}
                    disabled={deleting || !selectedRun} title="Borrar esta corrida">
              {deleting ? "Borrando…" : "Borrar"}
            </button>
          </div>
        )}
      </div>

      {deleteError && <div className="panel error">No se pudo borrar la corrida: {deleteError}</div>}

      <RunForm onRunComplete={() => fetchManifest(true)} />

      {manifestError && (
        <div className="panel error">
          No se pudo cargar data/manifest.json ({manifestError}). Generá una corrida desde el form.
        </div>
      )}
      {manifest && manifest.length === 0 && (
        <div className="panel error">No hay corridas en data/. Generá una desde el form de arriba.</div>
      )}
      {dataError && <div className="panel error">No se pudo cargar la corrida: {dataError}</div>}

      {runData && rows.length > 0 && (
        <>
          <div className="subtitle">
            {runData.date_start} → {runData.date_end} · intervalo 1 min (fijo) · equity diaria al cierre
          </div>

          <TopTable rows={rows} selectedKey={activeRowKey} onSelect={setSelectedRowKey} />

          {activeRowKey === "vanilla" ? (
            <>
              <TrailingTradesChart
                id="trades"
                title={`Precio ${runData.symbol} + operaciones — Vanilla`}
                price={runData.price}
                trades={runData.vanilla.trades}
                showTooltip={show}
                hideTooltip={hide}
              />
              <div className="panel">
                <p className="subtitle" style={{ margin: 0 }}>
                  Elegí una fila double-trailing para comparar su equity contra vanilla.
                </p>
              </div>
            </>
          ) : (
            activeCombo && (
              <>
                <TrailingTradesChart
                  id="trades"
                  title={`Precio ${runData.symbol} + operaciones — drop ${activeCombo.drop_pct}% / rise ${activeCombo.rise_pct}% / t.compra ${activeCombo.trail_buy_pct}% / t.venta ${activeCombo.trail_sell_pct}%`}
                  price={runData.price}
                  trades={activeCombo.trades}
                  showTooltip={show}
                  hideTooltip={hide}
                />
                <IndexedEquityChart
                  id="equity-compare"
                  price={runData.price}
                  vanillaEquity={runData.vanilla.equity}
                  trailingEquity={activeCombo.equity}
                  trailingLabel={`DT ${activeCombo.trail_buy_pct}%/${activeCombo.trail_sell_pct}%`}
                  showTooltip={show}
                  hideTooltip={hide}
                />
                <ExposureChart
                  id="exposure-compare"
                  price={runData.price}
                  committedCapital={runData.starting_cash}
                  vanillaEquity={runData.vanilla.equity}
                  trailingEquity={activeCombo.equity}
                  trailingLabel={`DT ${activeCombo.trail_buy_pct}%/${activeCombo.trail_sell_pct}%`}
                  showTooltip={show}
                  hideTooltip={hide}
                />
              </>
            )
          )}
        </>
      )}

      <Tooltip tooltip={tooltip} />
    </div>
  );
}
