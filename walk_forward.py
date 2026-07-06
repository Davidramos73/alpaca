import argparse
import itertools
import os
from datetime import datetime

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from optimize import (
    BUY_DROP_RANGE,
    MAX_BUYS,
    SELL_RISE_RANGE,
    STARTING_CASH,
    buy_hold_roi,
    load_bars,
    new_state,
    simulate,
)


def split_periods(df_1min: pd.DataFrame, period: str) -> list[dict]:
    ts = df_1min["timestamp"]
    if period == "week":
        iso = ts.dt.isocalendar()
        year_key, unit_key = iso["year"], iso["week"]
        label_prefix = "W"
    elif period == "month":
        year_key, unit_key = ts.dt.year, ts.dt.month
        label_prefix = "M"
    else:
        raise ValueError(f"period desconocido: {period!r} (usar 'week' o 'month')")

    periods = []
    for (year, unit), g in df_1min.groupby([year_key, unit_key], sort=True):
        g = g.reset_index(drop=True)
        periods.append({
            "label": f"{year}-{label_prefix}{unit:02d}",
            "start": g["timestamp"].iloc[0],
            "end":   g["timestamp"].iloc[-1],
            "df":    g,
        })
    return periods


def run_grid(df: pd.DataFrame, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]:
    results = []
    for interval in intervals:
        sub = df.iloc[::interval].reset_index(drop=True)
        for drop, rise in itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE):
            results.append(simulate(sub, MAX_BUYS, drop, rise, fee_pct, use_pool, buy_amount, interval))
    return results


def select_peak(results: list[dict]) -> dict:
    return max(results, key=lambda r: r["roi"])


def select_plateau(results: list[dict]) -> tuple[dict, float]:
    tabla = {
        (r["interval_minutes"], round(r["buy_drop_pct"] * 100), round(r["sell_rise_pct"] * 100)): r
        for r in results
    }
    mejor, mejor_score = None, float("-inf")
    for (m, d, s), r in tabla.items():
        vecinos = [
            tabla[(m, d + dd, s + ds)]["roi"]
            for dd in (-1, 0, 1)
            for ds in (-1, 0, 1)
            if (m, d + dd, s + ds) in tabla
        ]
        score = sum(vecinos) / len(vecinos)
        if score > mejor_score:
            mejor, mejor_score = r, score
    return mejor, mejor_score


def lag1_corr(values: list[float]) -> float:
    s = pd.Series(values, dtype=float)
    if len(s) < 3 or s.iloc[:-1].std() == 0 or s.iloc[1:].std() == 0:
        return float("nan")
    return float(s.corr(s.shift(1)))


def median_params(past_peaks: list[dict]) -> tuple[float, float, int]:
    drop = round(float(np.median([p["buy_drop_pct"] for p in past_peaks])) * 100) / 100
    rise = round(float(np.median([p["sell_rise_pct"] for p in past_peaks])) * 100) / 100
    interval = int(pd.Series([p["interval_minutes"] for p in past_peaks]).mode().iloc[0])
    return drop, rise, interval


def simulate_adaptive(period_dfs: list[pd.DataFrame], params_per_period: list[tuple[float, float, int]], fee_pct: float, use_pool: bool, buy_amount: float) -> dict:
    state = None
    result = None
    for df_period, (drop, rise, interval) in zip(period_dfs, params_per_period, strict=True):
        sub = df_period.iloc[::interval].reset_index(drop=True)
        result = simulate(sub, MAX_BUYS, drop, rise, fee_pct, use_pool, buy_amount, interval, state=state)
        state = result["state"]
    return result


def tournament(periods: list[dict], train_periods: int, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> tuple[dict, dict]:
    def params_of(r: dict) -> tuple[float, float, int]:
        return (r["buy_drop_pct"], r["sell_rise_pct"], r["interval_minutes"])

    app = range(train_periods, len(periods))
    app_dfs = [periods[i]["wk"]["df"] for i in app]

    planes = {"fija-mediana": [], "wf-pico": [], "wf-meseta": [], "oraculo": []}
    for i in app:
        planes["fija-mediana"].append(median_params([periods[j]["peak"] for j in range(i)]))

        if train_periods == 1:
            train_results = periods[i - 1]["results"]
        else:
            train_df = pd.concat(
                [periods[j]["wk"]["df"] for j in range(i - train_periods, i)],
                ignore_index=True,
            )
            train_results = run_grid(train_df, intervals, fee_pct, use_pool, buy_amount)

        planes["wf-pico"].append(params_of(select_peak(train_results)))
        planes["wf-meseta"].append(params_of(select_plateau(train_results)[0]))
        planes["oraculo"].append(params_of(periods[i]["peak"]))

    resultados = {
        nombre: simulate_adaptive(app_dfs, plan, fee_pct, use_pool, buy_amount)
        for nombre, plan in planes.items()
    }
    resultados["buy-hold"] = buy_hold_roi(pd.concat(app_dfs, ignore_index=True))
    return resultados, planes


def regret_series(periods: list[dict], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]:
    rows = []
    for i in range(1, len(periods)):
        prev = periods[i - 1]["peak"]
        d, r, m = prev["buy_drop_pct"], prev["sell_rise_pct"], prev["interval_minutes"]
        sub = periods[i]["wk"]["df"].iloc[::m].reset_index(drop=True)
        applied = simulate(sub, MAX_BUYS, d, r, fee_pct, use_pool, buy_amount, m)
        own = periods[i]["peak"]["roi"]
        rows.append({
            "label":       periods[i]["wk"]["label"],
            "own_roi":     own,
            "applied_roi": applied["roi"],
            "regret":      own - applied["roi"],
        })
    return rows


def run_analysis(df_1min: pd.DataFrame, intervals: list[int], train_periods: int, fee_pct: float, use_pool: bool, buy_amount: float, period: str = "week") -> dict:
    periods_list = split_periods(df_1min, period)
    if len(periods_list) <= train_periods + 1:
        raise SystemExit(f"Error: {len(periods_list)} período(s) de datos; se necesitan al menos {train_periods + 2}.")

    periods = []
    for n, p in enumerate(periods_list, 1):
        print(f"  Grid período {n}/{len(periods_list)} ({p['label']})", end="\r")
        results = run_grid(p["df"], intervals, fee_pct, use_pool, buy_amount)
        plateau, plateau_score = select_plateau(results)
        periods.append({
            "wk": p,
            "results": results,
            "peak": select_peak(results),
            "plateau": plateau,
            "plateau_score": plateau_score,
        })
    print()

    drops = [p["peak"]["buy_drop_pct"] for p in periods]
    rises = [p["peak"]["sell_rise_pct"] for p in periods]
    ints  = [p["peak"]["interval_minutes"] for p in periods]
    q75d, q25d = np.percentile(drops, [75, 25])
    q75r, q25r = np.percentile(rises, [75, 25])
    stats = {
        "median_drop": float(np.median(drops)),
        "median_rise": float(np.median(rises)),
        "std_drop":    float(np.std(drops)),
        "std_rise":    float(np.std(rises)),
        "iqr_drop":    float(q75d - q25d),
        "iqr_rise":    float(q75r - q25r),
        "corr_drop":   lag1_corr(drops),
        "corr_rise":   lag1_corr(rises),
        "interval_counts": dict(pd.Series(ints).value_counts().sort_index()),
    }

    regret = regret_series(periods, fee_pct, use_pool, buy_amount)
    torneo, planes = tournament(periods, train_periods, intervals, fee_pct, use_pool, buy_amount)

    fija = torneo["fija-mediana"]["roi"]
    adaptativas = {"WF-pico": torneo["wf-pico"]["roi"], "WF-meseta": torneo["wf-meseta"]["roi"]}
    mejor_adapt = max(adaptativas, key=adaptativas.get)
    if adaptativas[mejor_adapt] > fija:
        veredicto = (
            f"EL AUTO-AJUSTE SE JUSTIFICA: {mejor_adapt} ({adaptativas[mejor_adapt]:+.2f}%) supera a "
            f"Fija-mediana ({fija:+.2f}%). Techo teórico (Oráculo): {torneo['oraculo']['roi']:+.2f}%."
        )
    else:
        d, r, m = median_params([p["peak"] for p in periods])
        veredicto = (
            f"EL AUTO-AJUSTE NO SE JUSTIFICA: Fija-mediana ({fija:+.2f}%) le gana a "
            f"WF-pico ({adaptativas['WF-pico']:+.2f}%) y WF-meseta ({adaptativas['WF-meseta']:+.2f}%). "
            f"Recomendación: parámetros fijos drop={d*100:.0f}% rise={r*100:.0f}% intervalo={m} min."
        )

    bh = torneo["buy-hold"]["roi"]
    mejor_real = max(fija, adaptativas["WF-pico"], adaptativas["WF-meseta"])
    if mejor_real > bh:
        veredicto += f" Buy & hold de referencia: {bh:+.2f}% — el grid le gana en este rango."
    else:
        veredicto += (
            f" OJO: buy & hold ({bh:+.2f}%) supera a la mejor estrategia del grid "
            f"({mejor_real:+.2f}%) — el grid no agrega valor sobre comprar y sostener en este símbolo/rango."
        )

    return {"periods": periods, "stats": stats, "regret": regret,
            "torneo": torneo, "planes": planes, "veredicto": veredicto}


SEP  = "=" * 80
SEP2 = "-" * 80


def build_report(symbol: str, out: dict, train_periods: int, intervals: list[int], buy_amount: float, fee_pct: float, use_pool: bool, period: str) -> list[str]:
    periods, stats, regret = out["periods"], out["stats"], out["regret"]
    torneo, planes = out["torneo"], out["planes"]

    lines = [
        SEP,
        f"  WALK-FORWARD {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        SEP,
        f"  Períodos: {len(periods)}  ({periods[0]['wk']['label']} → {periods[-1]['wk']['label']})"
        f"   |   granularidad: {period}   |   train_periods: {train_periods}   |   intervalos: {intervals}",
        f"  Monto por compra: ${buy_amount:,.0f}   |   fee: {fee_pct*100:.3f}%   |   pool: {'ON' if use_pool else 'OFF'}",
        SEP,
        "",
        "  1) ESTABILIDAD DE LOS ÓPTIMOS POR PERÍODO",
        SEP2,
        f"  {'período':<10}  {'velas':>6}  {'drop':>5}  {'rise':>5}  {'min':>4}  {'ROI%':>8}   |  {'meseta d/r/min':>15}  {'ROI%':>8}",
        SEP2,
    ]
    for p in periods:
        pk, q = p["peak"], p["plateau"]
        lines.append(
            f"  {p['wk']['label']:<10}  {len(p['wk']['df']):>6}  "
            f"{pk['buy_drop_pct']*100:>4.0f}%  {pk['sell_rise_pct']*100:>4.0f}%  {pk['interval_minutes']:>4}  {pk['roi']:>+8.2f}   |  "
            f"{q['buy_drop_pct']*100:>4.0f}/{q['sell_rise_pct']*100:>3.0f}/{q['interval_minutes']:>4}  {q['roi']:>+8.2f}"
        )
    lines += [
        SEP2,
        f"  drop óptimo : mediana {stats['median_drop']*100:.1f}%  desvío {stats['std_drop']*100:.2f}pp  IQR {stats['iqr_drop']*100:.1f}pp  autocorr lag-1 {stats['corr_drop']:+.2f}",
        f"  rise óptimo : mediana {stats['median_rise']*100:.1f}%  desvío {stats['std_rise']*100:.2f}pp  IQR {stats['iqr_rise']*100:.1f}pp  autocorr lag-1 {stats['corr_rise']:+.2f}",
        f"  intervalos ganadores: {stats['interval_counts']}",
        f"  (n = {len(periods)} períodos: muestra chica, interpretar la autocorrelación con cautela)",
        "",
        "  REGRET (usar el óptimo del período anterior vs el propio, períodos aislados)",
        SEP2,
        f"  {'período':<10}  {'ROI propio':>10}  {'ROI aplicado':>12}  {'regret':>8}",
        SEP2,
    ]
    for row in regret:
        lines.append(f"  {row['label']:<10}  {row['own_roi']:>+10.2f}  {row['applied_roi']:>+12.2f}  {row['regret']:>8.2f}")
    regrets = [row["regret"] for row in regret]
    lines += [
        SEP2,
        f"  regret promedio {np.mean(regrets):.2f}pp  |  mediana {np.median(regrets):.2f}pp  |  peor {np.max(regrets):.2f}pp",
        "",
        "  2) TORNEO DE ESTRATEGIAS (portfolio continuo, períodos de aplicación: "
        f"{len(periods) - train_periods})",
        SEP2,
        f"  {'estrategia':<14}  {'ROI%':>8}  {'maxDD%':>7}  {'Ganancia':>12}  {'Capital':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Fees':>10}",
        SEP2,
    ]
    for nombre in ("fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"):
        r = torneo[nombre]
        lines.append(
            f"  {nombre:<14}  {r['roi']:>+8.2f}  {r.get('max_drawdown_pct', 0.0):>7.2f}  ${r['profit']:>+11,.0f}  ${r['total_equity']:>11,.0f}  "
            f"{r.get('buys', 0):>7}  {r.get('sells', 0):>6}  {r.get('open_positions', 0):>5}  ${r.get('total_fees', 0.0):>9,.0f}"
        )
    lines += [
        SEP2,
        "",
        "  PARÁMETROS USADOS POR PERÍODO (drop%/rise%/min)",
        SEP2,
        "  " + f"{'período':<10}" + "".join(f"  {n:>14}" for n in planes),
        SEP2,
    ]
    app_periods = periods[train_periods:]
    for k, p in enumerate(app_periods):
        celdas = "".join(
            f"  {planes[n][k][0]*100:>4.0f}/{planes[n][k][1]*100:>3.0f}/{planes[n][k][2]:>4}"
            for n in planes
        )
        lines.append(f"  {p['wk']['label']:<10}{celdas}")
    lines += [SEP2, "", "  3) VEREDICTO", SEP2, f"  {out['veredicto']}", SEP]
    return lines


def main():
    parser = argparse.ArgumentParser(description="Walk-forward: estabilidad de óptimos por período y torneo de estrategias")
    parser.add_argument("--symbol",      type=str,   default="TSLA")
    parser.add_argument("--date-start",  type=str,   default="2026-01-01")
    parser.add_argument("--date-end",    type=str,   default="2026-06-28")
    parser.add_argument("--buy-amount",  type=float, default=10_000.0)
    parser.add_argument("--fee-pct",     type=float, default=0.0)
    parser.add_argument("--no-profit-pool", action="store_true")
    parser.add_argument("--intervals",   type=str,   default="20", help="Intervalos de revisión en minutos, separados por coma")
    parser.add_argument("--period",      type=str,   default="week", choices=["week", "month"], help="Granularidad de los períodos (default: week)")
    parser.add_argument("--train-periods", type=int, default=1, help="Períodos previos usados para optimizar (default: 1)")
    args = parser.parse_args()

    if args.train_periods < 1:
        parser.error("--train-periods debe ser >= 1")

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    intervals  = sorted({max(1, int(v.strip())) for v in args.intervals.split(",") if v.strip()})
    use_pool   = not args.no_profit_pool

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    out = run_analysis(df_1min, intervals, args.train_periods, args.fee_pct, use_pool, args.buy_amount, period=args.period)

    lines = build_report(symbol, out, args.train_periods, intervals, args.buy_amount, args.fee_pct, use_pool, args.period)
    print("\n".join(lines))

    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"walkforward_{symbol}_{run_ts}.log"
    csv_path = f"walkforward_{symbol}_{run_ts}.csv"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    filas = [{
        "period_label":     p["wk"]["label"],
        "period_start":     p["wk"]["start"],
        "period_end":       p["wk"]["end"],
        "bars":             len(p["wk"]["df"]),
        "best_drop":        p["peak"]["buy_drop_pct"],
        "best_rise":        p["peak"]["sell_rise_pct"],
        "best_interval":    p["peak"]["interval_minutes"],
        "best_roi":         p["peak"]["roi"],
        "plateau_drop":     p["plateau"]["buy_drop_pct"],
        "plateau_rise":     p["plateau"]["sell_rise_pct"],
        "plateau_interval": p["plateau"]["interval_minutes"],
        "plateau_roi":      p["plateau"]["roi"],
    } for p in out["periods"]]
    pd.DataFrame(filas).to_csv(csv_path, index=False)

    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")


if __name__ == "__main__":
    main()
