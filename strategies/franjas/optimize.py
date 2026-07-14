import os
import sys
import argparse
import itertools
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ---------------------------------------------------------------------------
# Rangos de búsqueda
# ---------------------------------------------------------------------------
DROP_BASE_RANGE = [r / 100 for r in range(1, 7)]    # 1% … 6%
RISE_BASE_RANGE = [r / 100 for r in range(1, 9)]    # 1% … 8%
DROP_STEP_RANGE = [0.0, 0.01, 0.02]                 # incremento de drop por franja
RISE_STEP_RANGE = [0.0, 0.01, 0.02, 0.03]           # incremento de rise por franja
BAND1_RANGE     = [0.04, 0.06, 0.08]                # límite franja 0 → 1
BAND2_OFFSETS   = [0.03, 0.05]                      # límite franja 1 → 2 = band1 + offset

BUY_AMOUNT    = 10_000.0

LOGS_DIR = "logs"

# ---------------------------------------------------------------------------
# Simulación con franjas: el drop de la próxima compra y el rise de cada
# posición dependen de cuánto cayó el precio desde la referencia del ciclo
# (precio de la primera compra del ciclo; se resetea al vaciarse el grid).
# Franja 0: caída < band_bounds[0]; franja i: entre bounds[i-1] y bounds[i];
# última franja: caída >= bounds[-1]. drops/rises tienen len(bounds)+1.
# ---------------------------------------------------------------------------
def _band(drawdown: float, band_bounds) -> int:
    for i, b in enumerate(band_bounds):
        if drawdown < b:
            return i
    return len(band_bounds)

def simulate_franjas(df: pd.DataFrame, max_buys: int, drops, rises, band_bounds,
                     fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT,
                     on_trade=None, on_bar=None) -> dict:
    starting_cash = buy_amount * max_buys * (1.0 + fee_pct)
    cash        = starting_cash
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0
    held_qty    = 0.0
    invested    = 0.0
    cycle_ref   = None  # precio de la primera compra del ciclo
    band_buys   = [0] * (len(band_bounds) + 1)

    closes     = df["close"].to_numpy(dtype=float)
    timestamps = df["timestamp"].to_list()

    def _do_buy(price, timestamp, ev_type, band):
        nonlocal cash, held_qty, invested, profit_pool, total_fees, total_buys
        free_slots    = max_buys - len(purchases)
        bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
        effective_buy = buy_amount + bonus
        buy_fee = effective_buy * fee_pct
        if cash < effective_buy + buy_fee:
            return
        qty   = effective_buy / price
        cash -= effective_buy + buy_fee
        held_qty += qty
        invested += effective_buy
        if use_pool:
            profit_pool -= bonus
        total_fees += buy_fee
        total_buys += 1
        band_buys[band] += 1
        purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee,
                          "effective_buy": effective_buy, "timestamp": timestamp,
                          "order_id": total_buys, "band": band, "rise": rises[band]})
        if on_trade:
            on_trade({"type": ev_type, "price": price, "qty": qty, "fee": buy_fee,
                      "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                      "open_positions": len(purchases), "order_id": total_buys, "band": band})

    for i in range(len(closes)):
        price     = closes[i]
        timestamp = timestamps[i]

        if len(purchases) == 0:
            cycle_ref = price
            _do_buy(price, timestamp, "BUY_INIT", 0)
            if on_bar:
                on_bar(timestamp, cash + held_qty * price, invested)
            continue

        last        = purchases[-1]
        last_price  = last["price"]
        band_last   = _band(1.0 - last_price / cycle_ref, band_bounds)
        buy_target  = last_price * (1.0 - drops[band_last])
        sell_target = last_price * (1.0 + last["rise"])

        if price <= buy_target:
            if len(purchases) < max_buys:
                band_now = _band(1.0 - price / cycle_ref, band_bounds)
                _do_buy(price, timestamp, "BUY_GRID", band_now)

        elif price >= sell_target:
            sold      = purchases.pop()
            revenue   = sold["qty"] * price
            sell_fee  = revenue * fee_pct
            cash     += revenue - sell_fee
            held_qty -= sold["qty"]
            invested -= sold["effective_buy"]
            total_fees += sell_fee
            total_sells += 1
            profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
            if use_pool and profit > 0:
                profit_pool += profit
            if on_trade:
                on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                          "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                          "open_positions": len(purchases), "buy_price": sold["price"],
                          "profit": profit, "buy_timestamp": sold["timestamp"],
                          "order_id": sold["order_id"], "band": sold["band"]})

        if on_bar:
            on_bar(timestamp, cash + held_qty * price, invested)

    final_price    = float(closes[-1])
    holdings_value = sum(p["qty"] for p in purchases) * final_price
    total_equity   = cash + holdings_value
    profit         = total_equity - starting_cash
    roi            = (profit / starting_cash) * 100

    return {
        "drops":          list(drops),
        "rises":          list(rises),
        "band_bounds":    list(band_bounds),
        "fee_pct":        fee_pct,
        "starting_cash":  starting_cash,
        "roi":            roi,
        "profit":         profit,
        "total_equity":   total_equity,
        "total_fees":     total_fees,
        "buys":           total_buys,
        "sells":          total_sells,
        "open_positions": len(purchases),
        "band_buys":      band_buys,
    }

# ---------------------------------------------------------------------------
# Vanilla de referencia (idéntico a strategies/vanilla): franjas degeneradas
# ---------------------------------------------------------------------------
def simulate_vanilla(df, max_buys, buy_drop_pct, sell_rise_pct, fee_pct,
                     use_pool=True, buy_amount=BUY_AMOUNT):
    return simulate_franjas(df, max_buys,
                            drops=[buy_drop_pct], rises=[sell_rise_pct], band_bounds=[],
                            fee_pct=fee_pct, use_pool=use_pool, buy_amount=buy_amount)

# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
_WORKER_DF = None

def _init_worker(df_1min):
    global _WORKER_DF
    _WORKER_DF = df_1min

def _run_vanilla_combo(job):
    buy_drop, sell_rise, max_buys, fee_pct, use_pool, buy_amount = job
    return simulate_vanilla(_WORKER_DF, max_buys, buy_drop, sell_rise, fee_pct, use_pool, buy_amount)

def _run_franjas_combo(job):
    drops, rises, bounds, max_buys, fee_pct, use_pool, buy_amount = job
    return simulate_franjas(_WORKER_DF, max_buys, drops, rises, bounds,
                            fee_pct, use_pool, buy_amount)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Optimizador grid con drop/rise variable por franjas de caída")
    parser.add_argument("--symbol",     type=str,   default="TSLA")
    parser.add_argument("--date-start", type=str,   default="2026-01-01")
    parser.add_argument("--date-end",   type=str,   default="2026-06-28")
    parser.add_argument("--buy-amount", type=float, default=10_000.0)
    parser.add_argument("--max-buys",   type=int,   default=10)
    parser.add_argument("--fee-pct",    type=float, default=0.0)
    parser.add_argument("--no-profit-pool", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: credenciales no encontradas en .env")
        sys.exit(1)

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    if date_start >= date_end:
        print("Error: date-start debe ser anterior a date-end.")
        sys.exit(1)
    os.makedirs(LOGS_DIR, exist_ok=True)
    cache_path = os.path.join(LOGS_DIR, f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}_1Min.pkl")

    if os.path.exists(cache_path):
        print(f"Cargando datos desde caché ({cache_path})…")
        df_1min = pd.read_pickle(cache_path)
    else:
        print(f"Descargando datos históricos (1 minuto) de Alpaca para {symbol}…")
        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                               start=date_start, end=date_end)
        df_1min = client.get_stock_bars(req).df.reset_index()
        df_1min.to_pickle(cache_path)
        print(f"Datos guardados en caché ({cache_path})")

    if len(df_1min) == 0:
        print(f"Error: sin velas para {symbol} en el rango pedido.")
        sys.exit(1)
    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    fee_pct    = args.fee_pct
    buy_amount = args.buy_amount
    max_buys   = args.max_buys
    use_pool   = not args.no_profit_pool

    # Grilla de franjas: 3 franjas con drops/rises aritméticos + límites variables
    franja_combos = []
    for d1, dstep, r1, rstep, b1, boff in itertools.product(
            DROP_BASE_RANGE, DROP_STEP_RANGE, RISE_BASE_RANGE, RISE_STEP_RANGE,
            BAND1_RANGE, BAND2_OFFSETS):
        drops  = (d1, d1 + dstep, d1 + 2 * dstep)
        rises  = (r1, r1 + rstep, r1 + 2 * rstep)
        bounds = (b1, b1 + boff)
        franja_combos.append((drops, rises, bounds))
    # steps (0,0) duplican combos con distintos bounds → dedup
    franja_combos = list(dict.fromkeys(franja_combos))

    workers = os.cpu_count() or 1
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(df_1min,)) as executor:
        vanilla_combos = list(itertools.product([r / 100 for r in range(1, 11)],
                                                [r / 100 for r in range(1, 11)]))
        print(f"Fase vanilla: {len(vanilla_combos)} combinaciones drop/rise (referencia)…")
        vanilla_jobs = [(d, r, max_buys, fee_pct, use_pool, buy_amount) for d, r in vanilla_combos]
        vanilla_results = list(executor.map(_run_vanilla_combo, vanilla_jobs, chunksize=8))
        vanilla_results.sort(key=lambda r: r["roi"], reverse=True)
        vanilla_best = vanilla_results[0]

        total = len(franja_combos)
        print(f"Grilla franjas: {total} combinaciones "
              f"(max_buys = {max_buys}, buy_amount = ${buy_amount:,.0f}, fee = {fee_pct*100:.3f}%, "
              f"pool = {'ON' if use_pool else 'OFF'})…\n")
        jobs = [(drops, rises, bounds, max_buys, fee_pct, use_pool, buy_amount)
                for drops, rises, bounds in franja_combos]
        results = []
        for done, r in enumerate(executor.map(_run_franjas_combo, jobs, chunksize=8), 1):
            if done % 50 == 0 or done == total:
                print(f"  {done}/{total}", end="\r")
            results.append(r)
        results.sort(key=lambda r: r["roi"], reverse=True)

    top_n    = 20
    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.log")
    csv_path = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.csv")

    periodo_start = df_1min.iloc[0]["timestamp"].strftime("%Y-%m-%d")
    periodo_end   = df_1min.iloc[-1]["timestamp"].strftime("%Y-%m-%d")
    worst = results[-1]

    sep  = "=" * 110
    sep2 = "-" * 110
    header_row = (
        f"{'#':>3}  {'drops%':>12}  {'rises%':>12}  {'franjas%':>9}  {'ROI%':>8}  "
        f"{'Ganancia':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Compras/franja':>14}"
    )

    def _pcts(vals):
        return "/".join(f"{v*100:g}" for v in vals)

    def fmt_row(rank, r):
        return (
            f"{rank:>3}  "
            f"{_pcts(r['drops']):>12}  "
            f"{_pcts(r['rises']):>12}  "
            f"{_pcts(r['band_bounds']):>9}  "
            f"{r['roi']:>+8.2f}%  "
            f"${r['profit']:>+11,.0f}  "
            f"{r['buys']:>7}  "
            f"{r['sells']:>6}  "
            f"{r['open_positions']:>5}  "
            f"{'/'.join(str(b) for b in r['band_buys']):>14}"
        )

    lines = [
        sep,
        f"  OPTIMIZE FRANJAS {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        f"  Período analizado:  {periodo_start}  →  {periodo_end}",
        f"  Velas de 1 minuto:  {len(df_1min)}   |   Intervalo fijo: 1 min",
        f"  Capital comprometido: ${buy_amount * max_buys * (1.0 + fee_pct):,.2f}   |   Monto por compra: ${buy_amount:,.2f}",
        f"  max_buys:           {max_buys}",
        f"  Combinaciones evaluadas: {total} (franjas) + {len(vanilla_combos)} (vanilla referencia)",
        sep2,
        f"  REFERENCIA VANILLA (mejor drop/rise sin franjas)",
        f"  drop {vanilla_best['drops'][0]*100:.0f}% / rise {vanilla_best['rises'][0]*100:.0f}%  "
        f"ROI {vanilla_best['roi']:+.2f}%  Ganancia ${vanilla_best['profit']:+,.0f}  "
        f"Compras {vanilla_best['buys']}  Ventas {vanilla_best['sells']}",
        sep2,
        "",
        f"  TOP {top_n} COMBINACIONES FRANJAS (ordenadas por ROI)",
        sep2,
        header_row,
        sep2,
    ]
    for rank, r in enumerate(results[:top_n], 1):
        lines.append(fmt_row(rank, r))
    lines += [
        sep2,
        "",
        "  PEOR COMBINACIÓN",
        sep2,
        fmt_row(total, worst),
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
    console_lines = lines[:lines.index("  TODAS LAS COMBINACIONES (ordenadas por ROI)")]
    print("\n" + "\n".join(console_lines))
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log_content + "\n")
    pd.DataFrame(results).to_csv(csv_path, index=False)
    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")

if __name__ == "__main__":
    main()
