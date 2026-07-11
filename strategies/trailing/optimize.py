import os
import re
import sys
import argparse
import itertools
import json
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ---------------------------------------------------------------------------
# Rangos de búsqueda (máximo 10%)
# ---------------------------------------------------------------------------
MAX_BUYS           = 10                                 # fijo, no modificable
BUY_DROP_RANGE     = [r / 100 for r in range(1, 11)]   # 1% … 10%
SELL_RISE_RANGE    = [r / 100 for r in range(1, 11)]   # 1% … 10%

BUY_AMOUNT    = 10_000.0
STARTING_CASH = 100_000.0

LOGS_DIR = "logs"   # cache .pkl, .log y .csv de cada corrida

# ---------------------------------------------------------------------------
# Simulación (misma lógica que backtest.py, sin I/O)
# ---------------------------------------------------------------------------
def simulate(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, on_trade=None, on_bar=None) -> dict:
    cash        = STARTING_CASH
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0

    for _, row in df.iterrows():
        price = float(row["close"])

        if len(purchases) == 0:
            free_slots    = max_buys - len(purchases)
            bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
            effective_buy = BUY_AMOUNT + bonus
            qty     = effective_buy / price
            buy_fee = effective_buy * fee_pct
            cash   -= effective_buy + buy_fee
            if use_pool:
                profit_pool -= bonus
            total_fees += buy_fee
            total_buys += 1
            order_id = total_buys
            purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                               "timestamp": row["timestamp"], "order_id": order_id})
            if on_trade:
                on_trade({"type": "BUY_INIT", "price": price, "qty": qty, "fee": buy_fee,
                          "cash": cash, "pool": profit_pool, "timestamp": row["timestamp"],
                          "open_positions": len(purchases), "order_id": order_id})
            if on_bar:
                on_bar(row["timestamp"], cash + sum(p["qty"] for p in purchases) * price)
            continue

        last_price  = purchases[-1]["price"]
        buy_target  = last_price * (1.0 - buy_drop_pct)
        sell_target = last_price * (1.0 + sell_rise_pct)

        if price <= buy_target:
            if len(purchases) < max_buys:
                free_slots    = max_buys - len(purchases)
                bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
                effective_buy = BUY_AMOUNT + bonus
                qty     = effective_buy / price
                buy_fee = effective_buy * fee_pct
                cash   -= effective_buy + buy_fee
                if use_pool:
                    profit_pool -= bonus
                total_fees += buy_fee
                total_buys += 1
                order_id = total_buys
                purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                                   "timestamp": row["timestamp"], "order_id": order_id})
                if on_trade:
                    on_trade({"type": "BUY_GRID", "price": price, "qty": qty, "fee": buy_fee,
                              "cash": cash, "pool": profit_pool, "timestamp": row["timestamp"],
                              "open_positions": len(purchases), "order_id": order_id})

        elif price >= sell_target:
            sold      = purchases.pop()
            revenue   = sold["qty"] * price
            sell_fee  = revenue * fee_pct
            cash     += revenue - sell_fee
            total_fees += sell_fee
            total_sells += 1
            profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
            if use_pool and profit > 0:
                profit_pool += profit
            if on_trade:
                on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                          "cash": cash, "pool": profit_pool, "timestamp": row["timestamp"],
                          "open_positions": len(purchases),
                          "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                          "order_id": sold["order_id"]})

        if on_bar:
            on_bar(row["timestamp"], cash + sum(p["qty"] for p in purchases) * price)

    final_price    = float(df.iloc[-1]["close"])
    holdings_value = sum(p["qty"] for p in purchases) * final_price
    total_equity   = cash + holdings_value
    profit         = total_equity - STARTING_CASH
    roi            = (profit / STARTING_CASH) * 100

    return {
        "interval_minutes": interval_minutes,
        "max_buys":       max_buys,
        "buy_drop_pct":   buy_drop_pct,
        "sell_rise_pct":  sell_rise_pct,
        "fee_pct":        fee_pct,
        "roi":            roi,
        "profit":         profit,
        "total_equity":   total_equity,
        "total_fees":     total_fees,
        "buys":           total_buys,
        "sells":          total_sells,
        "open_positions": len(purchases),
    }

def simulate_trailing(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, trail_pct: float = 0.0, on_trade=None, on_bar=None) -> dict:
    """Como simulate(), pero al llegar a sell_rise_pct no vende: arma un
    trailing stop que sigue el pico del precio (vela a vela, no solo en
    checkpoints) y vende recién cuando el precio retrocede trail_pct desde
    ese pico. Mientras el trailing está armado no se evalúan compras ni
    ventas del grid. Usa precio real de ejecución en toda la contabilidad;
    trailing_capture (por venta y total) es una métrica de reporte que
    compara contra el sell_target que hubiera vendido la versión vanilla.
    df debe ser el histórico de 1 minuto completo, sin resamplear."""
    cash        = STARTING_CASH
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0
    trailing_capture_total = 0.0
    trailing_sells = 0
    trailing_captures = []

    trailing = None  # {"peak", "stop", "sell_target_ref"} cuando está armado

    for i, row in enumerate(df.itertuples(index=False)):
        price     = float(row.close)
        timestamp = row.timestamp

        if trailing is not None:
            if price > trailing["peak"]:
                trailing["peak"] = price
                trailing["stop"] = trailing["peak"] * (1.0 - trail_pct)
            if price <= trailing["stop"]:
                sold      = purchases.pop()
                revenue   = sold["qty"] * price
                sell_fee  = revenue * fee_pct
                cash     += revenue - sell_fee
                total_fees += sell_fee
                total_sells += 1
                profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
                if use_pool and profit > 0:
                    profit_pool += profit
                capture = sold["qty"] * (price - trailing["sell_target_ref"])
                trailing_capture_total += capture
                trailing_captures.append(capture)
                trailing_sells += 1
                if on_trade:
                    on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                              "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                              "open_positions": len(purchases),
                              "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                              "order_id": sold["order_id"], "trailing_capture": capture})
                trailing = None
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if i % interval_minutes != 0:
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if len(purchases) == 0:
            free_slots    = max_buys - len(purchases)
            bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
            effective_buy = buy_amount + bonus
            qty     = effective_buy / price
            buy_fee = effective_buy * fee_pct
            cash   -= effective_buy + buy_fee
            if use_pool:
                profit_pool -= bonus
            total_fees += buy_fee
            total_buys += 1
            order_id = total_buys
            purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                               "timestamp": timestamp, "order_id": order_id})
            if on_trade:
                on_trade({"type": "BUY_INIT", "price": price, "qty": qty, "fee": buy_fee,
                          "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                          "open_positions": len(purchases), "order_id": order_id})
        else:
            last_price  = purchases[-1]["price"]
            buy_target  = last_price * (1.0 - buy_drop_pct)
            sell_target = last_price * (1.0 + sell_rise_pct)

            if price <= buy_target:
                if len(purchases) < max_buys:
                    free_slots    = max_buys - len(purchases)
                    bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
                    effective_buy = buy_amount + bonus
                    qty     = effective_buy / price
                    buy_fee = effective_buy * fee_pct
                    cash   -= effective_buy + buy_fee
                    if use_pool:
                        profit_pool -= bonus
                    total_fees += buy_fee
                    total_buys += 1
                    order_id = total_buys
                    purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                                       "timestamp": timestamp, "order_id": order_id})
                    if on_trade:
                        on_trade({"type": "BUY_GRID", "price": price, "qty": qty, "fee": buy_fee,
                                  "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                                  "open_positions": len(purchases), "order_id": order_id})

            elif price >= sell_target:
                trailing = {"peak": price, "stop": price * (1.0 - trail_pct), "sell_target_ref": sell_target}

        if on_bar:
            on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)

    # Fin de datos con trailing activo: liquidar al último close disponible.
    if trailing is not None and purchases:
        price     = float(df.iloc[-1]["close"])
        timestamp = df.iloc[-1]["timestamp"]
        sold      = purchases.pop()
        revenue   = sold["qty"] * price
        sell_fee  = revenue * fee_pct
        cash     += revenue - sell_fee
        total_fees += sell_fee
        total_sells += 1
        profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
        if use_pool and profit > 0:
            profit_pool += profit
        capture = sold["qty"] * (price - trailing["sell_target_ref"])
        trailing_capture_total += capture
        trailing_captures.append(capture)
        trailing_sells += 1
        if on_trade:
            on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                      "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                      "open_positions": len(purchases),
                      "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                      "order_id": sold["order_id"], "trailing_capture": capture})

    final_price    = float(df.iloc[-1]["close"])
    holdings_value = sum(p["qty"] for p in purchases) * final_price
    total_equity   = cash + holdings_value
    profit         = total_equity - STARTING_CASH
    roi            = (profit / STARTING_CASH) * 100

    return {
        "interval_minutes": interval_minutes,
        "max_buys":       max_buys,
        "buy_drop_pct":   buy_drop_pct,
        "sell_rise_pct":  sell_rise_pct,
        "fee_pct":        fee_pct,
        "trail_pct":      trail_pct,
        "roi":            roi,
        "profit":         profit,
        "total_equity":   total_equity,
        "total_fees":     total_fees,
        "buys":           total_buys,
        "sells":          total_sells,
        "open_positions": len(purchases),
        "trailing_capture_total": trailing_capture_total,
        "trailing_sells":         trailing_sells,
        "trailing_captures":      trailing_captures,
    }

def daily_last(records: list[tuple]) -> list[dict]:
    """Reduce una serie (timestamp, valor) a un punto por día de calendario
    (el último valor visto ese día, que con datos intradía ordenados es el
    más cercano al cierre de mercado)."""
    daily: dict = {}
    for ts, value in records:
        daily[ts.date()] = value
    return [{"date": d.isoformat(), "value": v} for d, v in sorted(daily.items())]

BASE_EQUITY_RE = re.compile(r"^optimize_(?P<symbol>[^_]+)_(?P<run_ts>\d{8}_\d{6})_equity\.json$")
TRAIL_EQUITY_RE = re.compile(r"^optimize_(?P<symbol>[^_]+)_(?P<run_ts>\d{8}_\d{6})_trail_(?P<trail_pct>\d+(?:\.\d+)?)_equity\.json$")

def regenerate_manifest(out_dir: str) -> str:
    """Escanea out_dir (incluyendo subcarpetas por símbolo, ej. out_dir/TSLA/)
    agrupando cada corrida (símbolo + run_ts) con su JSON base (grid vanilla,
    usado como referencia) y sus JSON de trailing asociados, y regenera
    manifest.json para que el visor React sepa qué corridas puede listar en
    el dropdown. Un run sin JSON base (por ejemplo si quedó a medias) no se
    incluye."""
    runs: dict = {}

    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, out_dir).replace(os.sep, "/")

            m_trail = TRAIL_EQUITY_RE.match(name)
            if m_trail:
                key = (m_trail.group("symbol"), m_trail.group("run_ts"))
                run = runs.setdefault(key, {"trail_files": []})
                run["trail_files"].append({"trail_pct": float(m_trail.group("trail_pct")), "file": rel})
                continue

            m_base = BASE_EQUITY_RE.match(name)
            if not m_base:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            key = (m_base.group("symbol"), m_base.group("run_ts"))
            run = runs.setdefault(key, {"trail_files": []})
            run["symbol"]     = payload.get("symbol", m_base.group("symbol"))
            run["run_ts"]     = m_base.group("run_ts")
            run["date_start"] = payload.get("date_start")
            run["date_end"]   = payload.get("date_end")
            run["base_file"]  = rel

    entries = [run for run in runs.values() if "base_file" in run]
    for run in entries:
        run["trail_files"].sort(key=lambda t: t["trail_pct"])
    entries.sort(key=lambda run: run["run_ts"], reverse=True)

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    return manifest_path

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Optimizador de estrategia grid")
    parser.add_argument("--symbol",     type=str,   default="TSLA",       help="Símbolo a analizar (default: TSLA)")
    parser.add_argument("--date-start", type=str,   default="2026-01-01", help="Fecha inicio YYYY-MM-DD (default: 2026-01-01)")
    parser.add_argument("--date-end",   type=str,   default="2026-06-28", help="Fecha fin YYYY-MM-DD (default: 2026-06-28)")
    parser.add_argument("--buy-amount", type=float, default=10_000.0,     help="Monto base por compra en USD (default: 10000)")
    parser.add_argument("--fee-pct",    type=float, default=0.0,          help="Fee por operación sobre el monto (default: 0.0). Ej: 0.001 = 0.1%%")
    parser.add_argument("--no-profit-pool", action="store_true",          help="Desactivar reinversión de ganancias (modo clásico)")
    parser.add_argument("--intervals",  type=str,   default="20",         help="Lista de intervalos de revisión en minutos, separados por coma (default: 20). Ej: 1,5,15,20,30,60,120")
    parser.add_argument("--export-equity-json", action="store_true", help="Exportar la curva de equity diaria (al cierre) de cada combinación drop/rise a un JSON, para graficar después")
    parser.add_argument("--trail-pcts", type=str, default=None, help="Lista de % de trailing stop a comparar contra la mejor combinación, separados por coma (ej. 0.5,1,1.5,2). Requiere un solo --intervals.")
    parser.add_argument("--out-dir", type=str, default="viewer/public/data", help="Carpeta base donde escribir el JSON de equity para el visor React, organizado en out-dir/<símbolo>/ (default: viewer/public/data)")
    args = parser.parse_args()

    if args.export_equity_json and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --export-equity-json requiere un solo --intervals (no una lista).")
        sys.exit(1)

    if args.trail_pcts and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --trail-pcts requiere un solo --intervals (no una lista).")
        sys.exit(1)

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: credenciales no encontradas en .env")
        sys.exit(1)

    # --- Descargar datos una sola vez (caché por símbolo y período) ---
    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    if date_start >= date_end:
        print(f"Error: date-start ({args.date_start}) debe ser anterior a date-end ({args.date_end}).")
        sys.exit(1)
    os.makedirs(LOGS_DIR, exist_ok=True)
    cache_path = os.path.join(LOGS_DIR, f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}_1Min.pkl")

    if os.path.exists(cache_path):
        print(f"Cargando datos desde caché ({cache_path})…")
        df_1min = pd.read_pickle(cache_path)
    else:
        print(f"Descargando datos históricos (1 minuto) de Alpaca para {symbol}…")
        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=date_start,
            end=date_end,
        )
        bars    = client.get_stock_bars(req)
        df_1min = bars.df.reset_index()
        df_1min.to_pickle(cache_path)
        print(f"Datos guardados en caché ({cache_path})")

    if len(df_1min) == 0:
        print(f"Error: no se encontraron velas de 1 minuto para {symbol} entre {args.date_start} y {args.date_end}. "
              f"Verificá el símbolo y el rango de fechas (puede estar fuera del histórico disponible o no tener "
              f"días hábiles).")
        sys.exit(1)

    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    intervals = sorted({max(1, int(v.strip())) for v in args.intervals.split(",") if v.strip()})

    # --- Grid search ---
    combos = list(itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE))
    total_per_interval = len(combos)
    total = total_per_interval * len(intervals)
    fee_pct    = args.fee_pct
    buy_amount = args.buy_amount
    use_pool   = not args.no_profit_pool
    print(f"Evaluando {total_per_interval} combinaciones x {len(intervals)} intervalo(s) {intervals} min "
          f"(max_buys fijo = {MAX_BUYS}, buy_amount = ${buy_amount:,.0f}, fee = {fee_pct*100:.3f}%, pool = {'ON' if use_pool else 'OFF'})…\n")

    results = []
    equity_series = []  # solo se llena si --export-equity-json
    done = 0
    for interval_minutes in intervals:
        df = df_1min.iloc[::interval_minutes].reset_index(drop=True)
        for buy_drop, sell_rise in combos:
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  {done}/{total}", end="\r")
            if args.export_equity_json:
                bars = []
                r = simulate(df, MAX_BUYS, buy_drop, sell_rise, fee_pct, use_pool, buy_amount, interval_minutes,
                             on_bar=lambda ts, eq: bars.append((ts, eq)))
                equity_series.append({
                    "drop_pct": round(buy_drop * 100),
                    "rise_pct": round(sell_rise * 100),
                    "interval_minutes": interval_minutes,
                    "points": [{"date": p["date"], "equity": p["value"]} for p in daily_last(bars)],
                })
            else:
                r = simulate(df, MAX_BUYS, buy_drop, sell_rise, fee_pct, use_pool, buy_amount, interval_minutes)
            results.append(r)

    # --- Resultados ---
    results.sort(key=lambda r: r["roi"], reverse=True)

    top_n     = 20
    run_ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.log")
    csv_path  = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.csv")

    periodo_start = df_1min.iloc[0]["timestamp"].strftime("%Y-%m-%d")
    periodo_end   = df_1min.iloc[-1]["timestamp"].strftime("%Y-%m-%d")
    precio_inicio = float(df_1min.iloc[0]["close"])
    precio_fin    = float(df_1min.iloc[-1]["close"])
    best          = results[0]
    worst         = results[-1]

    best_trades = []
    if args.export_equity_json:
        best_df = df_1min.iloc[::best["interval_minutes"]].reset_index(drop=True)

        def _capture_trade(ev):
            trade = {
                "type":     "SELL" if ev["type"] == "SELL" else "BUY",
                "date":     ev["timestamp"].strftime("%Y-%m-%d"),
                "time":     ev["timestamp"].strftime("%H:%M"),
                "price":    ev["price"],
                "order_id": ev["order_id"],
            }
            if ev["type"] == "SELL":
                trade["buy_price"] = ev["buy_price"]
                trade["profit"]    = ev["profit"]
                trade["buy_date"]  = ev["buy_timestamp"].strftime("%Y-%m-%d")
                trade["buy_time"]  = ev["buy_timestamp"].strftime("%H:%M")
            best_trades.append(trade)

        simulate(best_df, MAX_BUYS, best["buy_drop_pct"], best["sell_rise_pct"], fee_pct, use_pool, buy_amount,
                 best["interval_minutes"], on_trade=_capture_trade)

    trail_pcts = []
    trailing_results = []
    if args.trail_pcts:
        trail_pcts = sorted({float(v.strip()) / 100 for v in args.trail_pcts.split(",") if v.strip()})
        for trail_pct in trail_pcts:
            trailing_results.append(
                simulate_trailing(df_1min, MAX_BUYS, best["buy_drop_pct"], best["sell_rise_pct"], fee_pct,
                                   use_pool, buy_amount, best["interval_minutes"], trail_pct=trail_pct)
            )

    best_by_interval = {}
    for r in results:
        m = r["interval_minutes"]
        if m not in best_by_interval or r["roi"] > best_by_interval[m]["roi"]:
            best_by_interval[m] = r

    sep  = "=" * 80
    sep2 = "-" * 80

    header_row = (
        f"{'#':>3}  {'min':>5}  {'drop%':>6}  {'rise%':>6}  {'ROI%':>8}  "
        f"{'Ganancia':>12}  {'Capital':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}"
    )

    def fmt_row(rank, r):
        return (
            f"{rank:>3}  "
            f"{r['interval_minutes']:>5}  "
            f"{r['buy_drop_pct']*100:>5.0f}%  "
            f"{r['sell_rise_pct']*100:>5.0f}%  "
            f"{r['roi']:>+8.2f}%  "
            f"${r['profit']:>+11,.0f}  "
            f"${r['total_equity']:>11,.0f}  "
            f"{r['buys']:>7}  "
            f"{r['sells']:>6}  "
            f"{r['open_positions']:>5}"
        )

    lines = [
        sep,
        f"  OPTIMIZE {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        f"  Período analizado:  {periodo_start}  →  {periodo_end}",
        f"  Precio {symbol} inicio: ${precio_inicio:.2f}   |   Precio {symbol} fin: ${precio_fin:.2f}",
        f"  Velas de 1 minuto:  {len(df_1min)}",
        f"  Intervalos evaluados (min): {intervals}",
        f"  Capital inicial:    ${STARTING_CASH:,.2f}   |   Monto por compra: ${buy_amount:,.2f}",
        f"  max_buys (fijo):    {MAX_BUYS}",
        f"  Combinaciones evaluadas: {total}  "
        f"({total_per_interval} combos drop/rise x {len(intervals)} intervalo(s))",
        sep,
        "",
        f"  MEJOR COMBINACIÓN POR INTERVALO (ordenado por ROI)",
        sep2,
        header_row,
        sep2,
    ]

    for rank, m in enumerate(sorted(best_by_interval, key=lambda m: best_by_interval[m]["roi"], reverse=True), 1):
        lines.append(fmt_row(rank, best_by_interval[m]))

    lines += [
        sep2,
        "",
        f"  TOP {top_n} COMBINACIONES GLOBALES (ordenadas por ROI)",
        sep2,
        header_row,
        sep2,
    ]

    for rank, r in enumerate(results[:top_n], 1):
        lines.append(fmt_row(rank, r))

    lines += [
        sep2,
        "",
        "  MEJOR COMBINACIÓN",
        sep2,
        f"  intervalo     : {best['interval_minutes']} min",
        f"  buy_drop_pct  : {best['buy_drop_pct']*100:.0f}%",
        f"  sell_rise_pct : {best['sell_rise_pct']*100:.0f}%",
        f"  ROI           : {best['roi']:+.2f}%",
        f"  Ganancia      : ${best['profit']:+,.2f}",
        f"  Capital final : ${best['total_equity']:,.2f}",
        f"  Compras/Ventas: {best['buys']} / {best['sells']}",
        f"  Pos. abiertas : {best['open_positions']}",
        "",
        "  PEOR COMBINACIÓN",
        sep2,
        f"  intervalo     : {worst['interval_minutes']} min",
        f"  buy_drop_pct  : {worst['buy_drop_pct']*100:.0f}%",
        f"  sell_rise_pct : {worst['sell_rise_pct']*100:.0f}%",
        f"  ROI           : {worst['roi']:+.2f}%",
        f"  Ganancia      : ${worst['profit']:+,.2f}",
        f"  Capital final : ${worst['total_equity']:,.2f}",
        f"  Compras/Ventas: {worst['buys']} / {worst['sells']}",
        f"  Pos. abiertas : {worst['open_positions']}",
        "",
        "  TODAS LAS COMBINACIONES (ordenadas por ROI)",
        sep2,
        header_row,
        sep2,
    ]

    for rank, r in enumerate(results, 1):
        lines.append(fmt_row(rank, r))

    lines += [sep, f"  CSV completo: {csv_path}", sep]

    log_content = "\n".join(lines)

    # --- Imprimir en consola ---
    console_lines = lines[:lines.index("  TODAS LAS COMBINACIONES (ordenadas por ROI)")]
    print("\n" + "\n".join(console_lines))

    # --- Escribir log ---
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log_content + "\n")

    # --- Guardar CSV completo ---
    pd.DataFrame(results).to_csv(csv_path, index=False)

    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")

    price_daily = None
    symbol_dir  = None

    if args.export_equity_json:
        symbol_dir = os.path.join(args.out_dir, symbol)
        os.makedirs(symbol_dir, exist_ok=True)
        price_daily = daily_last(zip(df_1min["timestamp"], df_1min["close"].astype(float)))
        equity_json_name = f"optimize_{symbol}_{run_ts}_equity.json"
        equity_json_path = os.path.join(symbol_dir, equity_json_name)
        payload = {
            "symbol":       symbol,
            "date_start":   periodo_start,
            "date_end":     periodo_end,
            "interval_minutes": intervals[0],
            "starting_cash": STARTING_CASH,
            "price":        [{"date": p["date"], "close": p["value"]} for p in price_daily],
            "series":       equity_series,
            "best_combo":   {"drop_pct": round(best["buy_drop_pct"] * 100), "rise_pct": round(best["sell_rise_pct"] * 100)},
            "best_trades":  best_trades,
        }
        with open(equity_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"JSON de equity   : {equity_json_path}")

        # --- Generar JSONs para trailing (si se solicita) ---
    if args.export_equity_json and args.trail_pcts:
        # Reutilizamos el precio diario ya calculado (price_daily)
        for trail_pct in trail_pcts:
            bars_trail = []
            trades_trail = []

            def on_bar(ts, eq):
                bars_trail.append((ts, eq))

            def on_trade(ev):
                trade = {
                    "type":     ev["type"],   # "BUY_INIT", "BUY_GRID", "SELL"
                    "date":     ev["timestamp"].strftime("%Y-%m-%d"),
                    "time":     ev["timestamp"].strftime("%H:%M"),
                    "price":    ev["price"],
                    "order_id": ev["order_id"],
                }
                if ev["type"] == "SELL":
                    trade["buy_price"] = ev["buy_price"]
                    trade["profit"]    = ev["profit"]
                    trade["buy_date"]  = ev["buy_timestamp"].strftime("%Y-%m-%d")
                    trade["buy_time"]  = ev["buy_timestamp"].strftime("%H:%M")
                    if "trailing_capture" in ev:
                        trade["trailing_capture"] = ev["trailing_capture"]

                trades_trail.append(trade)

            # Ejecutar simulación con trailing
            r_trail = simulate_trailing(
                df_1min,
                MAX_BUYS,
                best["buy_drop_pct"],
                best["sell_rise_pct"],
                fee_pct,
                use_pool,
                buy_amount,
                best["interval_minutes"],
                trail_pct=trail_pct,
                on_bar=on_bar,
                on_trade=on_trade
            )

            # Reducir equity a diario
            equity_daily_trail = daily_last(bars_trail)

            # Construir payload
            trail_payload = {
                "symbol":       symbol,
                "date_start":   periodo_start,
                "date_end":     periodo_end,
                "interval_minutes": best["interval_minutes"],
                "starting_cash": STARTING_CASH,
                "trail_pct":    trail_pct,
                "buy_drop_pct": best["buy_drop_pct"],
                "sell_rise_pct": best["sell_rise_pct"],
                "price":        [{"date": p["date"], "close": p["value"]} for p in price_daily],
                "equity":       [{"date": p["date"], "equity": p["value"]} for p in equity_daily_trail],
                "trades":       trades_trail,
                # Métricas adicionales
                "roi":          r_trail["roi"],
                "profit":       r_trail["profit"],
                "total_equity": r_trail["total_equity"],
                "total_fees":   r_trail["total_fees"],
                "buys":         r_trail["buys"],
                "sells":        r_trail["sells"],
                "open_positions": r_trail["open_positions"],
                "trailing_capture_total": r_trail["trailing_capture_total"],
                "trailing_sells":         r_trail["trailing_sells"],
            }

            trail_json_name = f"optimize_{symbol}_{run_ts}_trail_{trail_pct*100:.1f}_equity.json"
            trail_json_path = os.path.join(symbol_dir, trail_json_name)
            with open(trail_json_path, "w", encoding="utf-8") as f:
                json.dump(trail_payload, f)
            print(f"JSON de equity con trailing {trail_pct*100:.1f}%: {trail_json_path}")

    if args.export_equity_json:
        manifest_path = regenerate_manifest(args.out_dir)
        print(f"Manifest visor   : {manifest_path}")

    if trailing_results:
        trail_sep = "-" * 80
        trail_lines = [
            "",
            f"  COMPARACIÓN TRAILING STOP (mejor combo: drop {best['buy_drop_pct']*100:.0f}% / "
            f"rise {best['sell_rise_pct']*100:.0f}% / interval {best['interval_minutes']} min)",
            trail_sep,
            f"  {'ESTRATEGIA':<16}  {'ROI':>9}  {'Compras':>7}  {'Ventas':>6}  {'Trailing capture':>17}",
            trail_sep,
            f"  {'vanilla':<16}  {best['roi']:>+8.2f}%  {best['buys']:>7}  {best['sells']:>6}  {'—':>17}",
        ]
        for trail_pct, r in zip(trail_pcts, trailing_results):
            label = f"trail {trail_pct*100:.1f}%"
            capture_str = "$" + format(r["trailing_capture_total"], "+,.0f")
            trail_lines.append(
                f"  {label:<16}  {r['roi']:>+8.2f}%  {r['buys']:>7}  {r['sells']:>6}  {capture_str:>17}"
            )
        trail_lines.append(trail_sep)
        trail_content = "\n".join(trail_lines)
        print(trail_content)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(trail_content + "\n")

if __name__ == "__main__":
    main()
