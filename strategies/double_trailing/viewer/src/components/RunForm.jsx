import { useState } from "react";

const DEFAULTS = {
  symbol: "TSLA",
  date_start: "2026-01-01",
  date_end: "2026-06-28",
  buy_amount: 10000,
  max_buys: 10,
  fee_pct: 0,
  trail_buy_pcts: "0.5,1,1.5",
  trail_sell_pcts: "0.5,1,1.5",
};

const TRAIL_PCTS_RE = /^\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*$/;

export default function RunForm({ onRunComplete }) {
  const [form, setForm] = useState(DEFAULTS);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    for (const field of ["trail_buy_pcts", "trail_sell_pcts"]) {
      if (!TRAIL_PCTS_RE.test(form[field].trim())) {
        setError(`${field === "trail_buy_pcts" ? "Trailing compra" : "Trailing venta"} inválido — usá números positivos separados por coma (ej. 0.5,1,1.5)`);
        return;
      }
    }
    setRunning(true);
    setError(null);
    try {
      const res = await fetch("/api/run-optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      await onRunComplete();
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <form className="panel run-form" onSubmit={handleSubmit}>
      <h2>Nueva corrida</h2>
      <div className="run-form-grid">
        <label>
          Símbolo
          <input type="text" value={form.symbol}
                 onChange={(e) => update("symbol", e.target.value.toUpperCase())}
                 disabled={running} required />
        </label>
        <label>
          Desde
          <input type="date" value={form.date_start}
                 onChange={(e) => update("date_start", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Hasta
          <input type="date" value={form.date_end}
                 onChange={(e) => update("date_end", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Monto por compra ($)
          <input type="number" min="1" step="1" value={form.buy_amount}
                 onChange={(e) => update("buy_amount", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Compras máximas simultáneas
          <input type="number" min="1" step="1" value={form.max_buys}
                 onChange={(e) => update("max_buys", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Fee (%)
          <input type="number" min="0" step="0.01" value={form.fee_pct * 100}
                 onChange={(e) => update("fee_pct", Number(e.target.value) / 100)}
                 disabled={running} required />
        </label>
        <label>
          Trailing compra % (rebote)
          <input type="text" value={form.trail_buy_pcts}
                 onChange={(e) => update("trail_buy_pcts", e.target.value)}
                 disabled={running} placeholder="0.5,1,1.5" required />
        </label>
        <label>
          Trailing venta % (retroceso)
          <input type="text" value={form.trail_sell_pcts}
                 onChange={(e) => update("trail_sell_pcts", e.target.value)}
                 disabled={running} placeholder="0.5,1,1.5" required />
        </label>
      </div>
      <button type="submit" disabled={running}>
        {running ? "Ejecutando…" : "Generar"}
      </button>
      {running && (
        <span className="run-status">
          Corriendo la grilla 4D con intervalo fijo de 1 minuto — puede tardar varios minutos…
        </span>
      )}
      {error && <div className="run-error">{error}</div>}
    </form>
  );
}
