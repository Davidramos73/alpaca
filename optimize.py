import os
import argparse
import itertools
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

# ---------------------------------------------------------------------------
# Simulación (misma lógica que backtest.py, sin I/O)
# ---------------------------------------------------------------------------
def new_state(starting_cash: float = STARTING_CASH) -> dict:
    return {
        "cash":        starting_cash,
        "purchases":   [],
        "profit_pool": 0.0,
        "total_buys":  0,
        "total_sells": 0,
        "total_fees":  0.0,
    }

def simulate(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, state: dict | None = None) -> dict:
    if state is None:
        state = new_state()
    cash        = state["cash"]
    purchases   = list(state["purchases"])
    profit_pool = state["profit_pool"]
    total_buys  = state["total_buys"]
    total_sells = state["total_sells"]
    total_fees  = state["total_fees"]

    for _, row in df.iterrows():
        price = float(row["close"])

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
            purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy})
            total_buys += 1
            continue

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
                purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy})
                total_buys += 1

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
        "state": {
            "cash":        cash,
            "purchases":   purchases,
            "profit_pool": profit_pool,
            "total_buys":  total_buys,
            "total_sells": total_sells,
            "total_fees":  total_fees,
        },
    }

# ---------------------------------------------------------------------------
# Carga de datos históricos (con caché en disco)
# ---------------------------------------------------------------------------
def load_bars(symbol: str, date_start: datetime, date_end: datetime, api_key: str, secret_key: str) -> pd.DataFrame:
    cache_path = f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}_1Min.pkl"
    if os.path.exists(cache_path):
        print(f"Cargando datos desde caché ({cache_path})…")
        return pd.read_pickle(cache_path)

    print(f"Descargando datos históricos (1 minuto) de Alpaca para {symbol}…")
    client = StockHistoricalDataClient(api_key, secret_key)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=date_start,
        end=date_end,
    )
    df = client.get_stock_bars(req).df.reset_index()
    df.to_pickle(cache_path)
    print(f"Datos guardados en caché ({cache_path})")
    return df

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
    args = parser.parse_args()

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: credenciales no encontradas en .env")
        return

    # --- Descargar datos una sola vez (caché por símbolo y período) ---
    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    df_1min    = load_bars(symbol, date_start, date_end, api_key, secret_key)

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
    done = 0
    for interval_minutes in intervals:
        df = df_1min.iloc[::interval_minutes].reset_index(drop=True)
        for buy_drop, sell_rise in combos:
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  {done}/{total}", end="\r")
            results.append(simulate(df, MAX_BUYS, buy_drop, sell_rise, fee_pct, use_pool, buy_amount, interval_minutes))

    # --- Resultados ---
    results.sort(key=lambda r: r["roi"], reverse=True)

    top_n     = 20
    run_ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = f"optimize_{symbol}_{run_ts}.log"
    csv_path  = f"optimize_{symbol}_{run_ts}.csv"

    periodo_start = df_1min.iloc[0]["timestamp"].strftime("%Y-%m-%d")
    periodo_end   = df_1min.iloc[-1]["timestamp"].strftime("%Y-%m-%d")
    precio_inicio = float(df_1min.iloc[0]["close"])
    precio_fin    = float(df_1min.iloc[-1]["close"])
    best          = results[0]
    worst         = results[-1]

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

if __name__ == "__main__":
    main()
