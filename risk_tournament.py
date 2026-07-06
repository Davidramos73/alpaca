import argparse
import os
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

from optimize import MAX_BUYS, buy_hold_roi, load_bars, simulate

SEP  = "=" * 96
SEP2 = "-" * 96


def build_variants() -> list[dict]:
    """Matriz de variantes del torneo (spec 2026-07-06): baseline + cada
    mecanismo aislado. Sin combinaciones en esta versión."""
    variants = [{"name": "baseline", "params": {}}]
    for cd in (390, 780, 1950):
        variants.append({"name": f"cooldown-{cd}min", "params": {"cooldown_minutes": cd}})
    for n in (2, 3):
        for x in (0.20, 0.30):
            variants.append({
                "name": f"reserva-{n}slots-{int(x * 100)}pct",
                "params": {"reserved_slots": n, "deep_drop_pct": x},
            })
    for t in (0.15, 0.25):
        variants.append({"name": f"breaker-{int(t * 100)}pct", "params": {"breaker_dd_pct": t}})
    return variants


def main():
    parser = argparse.ArgumentParser(description="Torneo de mecanismos anti-crash sobre datos históricos")
    parser.add_argument("--symbol",        type=str,   default="TSLA")
    parser.add_argument("--date-start",    type=str,   default="2024-07-01")
    parser.add_argument("--date-end",      type=str,   default="2026-07-01")
    parser.add_argument("--buy-drop-pct",  type=float, default=0.01)
    parser.add_argument("--sell-rise-pct", type=float, default=0.03)
    parser.add_argument("--interval",      type=int,   default=20, help="Intervalo de revisión en minutos (un solo valor)")
    parser.add_argument("--buy-amount",    type=float, default=10_000.0)
    parser.add_argument("--fee-pct",       type=float, default=0.0)
    parser.add_argument("--no-profit-pool", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    interval   = max(1, args.interval)
    use_pool   = not args.no_profit_pool

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    df = df_1min.iloc[::interval].reset_index(drop=True)
    print(f"Velas de 1 minuto: {len(df_1min)} | evaluadas al intervalo de {interval} min: {len(df)}\n")

    filas = []
    for v in build_variants():
        r = simulate(df, MAX_BUYS, args.buy_drop_pct, args.sell_rise_pct, args.fee_pct,
                     use_pool=use_pool, buy_amount=args.buy_amount,
                     interval_minutes=interval, **v["params"])
        filas.append({"name": v["name"], "params": v["params"], **{
            k: r[k] for k in ("roi", "max_drawdown_pct", "profit", "total_equity",
                              "buys", "sells", "open_positions", "total_fees")
        }})

    bh = buy_hold_roi(df_1min)
    baseline = filas[0]

    lines = [
        SEP,
        f"  RISK TOURNAMENT {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        SEP,
        f"  Rango: {df_1min.iloc[0]['timestamp'].strftime('%Y-%m-%d')} → {df_1min.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}"
        f"   |   intervalo: {interval} min   |   drop {args.buy_drop_pct*100:.0f}% / rise {args.sell_rise_pct*100:.0f}%",
        f"  Monto por compra: ${args.buy_amount:,.0f}   |   fee: {args.fee_pct*100:.3f}%   |   pool: {'ON' if use_pool else 'OFF'}   |   max_buys: {MAX_BUYS}",
        SEP,
        "",
        "  RESULTADOS POR VARIANTE",
        SEP2,
        f"  {'variante':<24}  {'ROI%':>8}  {'maxDD%':>7}  {'Ganancia':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Fees':>10}",
        SEP2,
    ]
    for f in filas:
        lines.append(
            f"  {f['name']:<24}  {f['roi']:>+8.2f}  {f['max_drawdown_pct']:>7.2f}  ${f['profit']:>+11,.0f}  "
            f"{f['buys']:>7}  {f['sells']:>6}  {f['open_positions']:>5}  ${f['total_fees']:>9,.0f}"
        )
    lines.append(
        f"  {'buy-hold (ref.)':<24}  {bh['roi']:>+8.2f}  {bh['max_drawdown_pct']:>7.2f}  ${bh['profit']:>+11,.0f}  "
        f"{'—':>7}  {'—':>6}  {'—':>5}  {'—':>10}"
    )
    lines += [
        SEP2,
        "",
        "  DELTAS vs BASELINE (ΔmaxDD positivo = protege; ΔROI positivo = gana más)",
        SEP2,
        f"  {'variante':<24}  {'ΔROI(pp)':>9}  {'ΔmaxDD(pp)':>11}",
        SEP2,
    ]
    for f in filas[1:]:
        d_roi = f["roi"] - baseline["roi"]
        d_dd  = baseline["max_drawdown_pct"] - f["max_drawdown_pct"]
        lines.append(f"  {f['name']:<24}  {d_roi:>+9.2f}  {d_dd:>+11.2f}")
    lines += [SEP2, "", f"  Baseline: ROI {baseline['roi']:+.2f}% / maxDD {baseline['max_drawdown_pct']:.2f}%"
              f"   |   Buy & hold: ROI {bh['roi']:+.2f}% / maxDD {bh['max_drawdown_pct']:.2f}%", SEP]

    print("\n".join(lines))

    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"risktournament_{symbol}_{run_ts}.log"
    csv_path = f"risktournament_{symbol}_{run_ts}.csv"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    pd.DataFrame(filas).to_csv(csv_path, index=False)
    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")


if __name__ == "__main__":
    main()
