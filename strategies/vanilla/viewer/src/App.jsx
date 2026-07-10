import { useCallback, useEffect, useState } from "react";
import PriceChart from "./components/PriceChart";
import TradesChart from "./components/TradesChart";
import Top5Chart from "./components/Top5Chart";
import ResultsTable from "./components/ResultsTable";
import RunForm from "./components/RunForm";
import Tooltip from "./components/Tooltip";
import { useTooltip } from "./hooks/useTooltip";

function runLabel(entry) {
  return `${entry.symbol} · ${entry.date_start} → ${entry.date_end} · corrida ${entry.run_ts}`;
}

export default function App() {
  const [manifest, setManifest] = useState(null);
  const [manifestError, setManifestError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [data, setData] = useState(null);
  const [dataError, setDataError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const { tooltip, show, hide } = useTooltip();

  const fetchManifest = useCallback(async (selectNewest) => {
    try {
      const r = await fetch("/data/manifest.json", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const entries = await r.json();
      setManifest(entries);
      setManifestError(null);
      if (selectNewest) {
        if (entries.length > 0) setSelectedFile(entries[0].file);
        else {
          setSelectedFile(null);
          setData(null);
        }
      }
    } catch (err) {
      setManifestError(err.message);
    }
  }, []);

  useEffect(() => {
    fetchManifest(true);
  }, [fetchManifest]);

  async function handleDelete() {
    if (!selectedFile) return;
    const entry = manifest?.find((e) => e.file === selectedFile);
    const label = entry ? runLabel(entry) : selectedFile;
    if (!window.confirm(`¿Borrar esta corrida?\n\n${label}`)) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await fetch("/api/delete-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: selectedFile }),
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
    if (!selectedFile) return;
    setData(null);
    setDataError(null);
    fetch(`/data/${selectedFile}`, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((err) => setDataError(err.message));
  }, [selectedFile]);

  return (
    <div className="app">
      <div className="app-header">
        <h1>Equity Viewer</h1>
        {manifest && manifest.length > 0 && (
          <div className="run-picker">
            <select value={selectedFile ?? ""} onChange={(e) => setSelectedFile(e.target.value)}>
              {manifest.map((entry) => (
                <option key={entry.file} value={entry.file}>
                  {runLabel(entry)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="delete-run-btn"
              onClick={handleDelete}
              disabled={deleting || !selectedFile}
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
          apuntando a esta carpeta.
        </div>
      )}
      {manifest && manifest.length === 0 && (
        <div className="panel error">
          No hay corridas en data/. Corré optimize.py con --export-equity-json --out-dir viewer/public/data.
        </div>
      )}
      {dataError && (
        <div className="panel error">
          No se pudo cargar {selectedFile}: {dataError}
        </div>
      )}

      {data && (
        <>
          <div className="subtitle">
            {data.date_start} → {data.date_end} · intervalo {data.interval_minutes} min · equity diaria al cierre de
            mercado
          </div>
          <div className="subtitle">
            Tip: arrastrá para hacer zoom · scroll para acercar/alejar · doble click para volver a la vista completa
          </div>

          <PriceChart id="price" title={`Precio ${data.symbol}`} price={data.price} showTooltip={show} hideTooltip={hide} />

          <TradesChart
            id="trades"
            title={`Precio ${data.symbol} + compras/ventas`}
            price={data.price}
            trades={data.best_trades || []}
            bestCombo={data.best_combo}
            showTooltip={show}
            hideTooltip={hide}
          />

          <Top5Chart
            id="top5"
            price={data.price}
            series={data.series}
            startingCash={data.starting_cash}
            showTooltip={show}
            hideTooltip={hide}
          />

          <ResultsTable series={data.series} />
        </>
      )}

      <Tooltip tooltip={tooltip} />
    </div>
  );
}
