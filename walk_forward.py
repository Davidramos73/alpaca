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
    load_bars,
    new_state,
    simulate,
)


def split_weeks(df_1min: pd.DataFrame) -> list[dict]:
    iso = df_1min["timestamp"].dt.isocalendar()
    weeks = []
    for (year, week), g in df_1min.groupby([iso["year"], iso["week"]], sort=True):
        g = g.reset_index(drop=True)
        weeks.append({
            "label": f"{year}-W{week:02d}",
            "start": g["timestamp"].iloc[0],
            "end":   g["timestamp"].iloc[-1],
            "df":    g,
        })
    return weeks


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


def simulate_adaptive(week_dfs: list[pd.DataFrame], params_per_week: list[tuple[float, float, int]], fee_pct: float, use_pool: bool, buy_amount: float) -> dict:
    state = None
    result = None
    for df_week, (drop, rise, interval) in zip(week_dfs, params_per_week, strict=True):
        sub = df_week.iloc[::interval].reset_index(drop=True)
        result = simulate(sub, MAX_BUYS, drop, rise, fee_pct, use_pool, buy_amount, interval, state=state)
        state = result["state"]
    return result


def tournament(weekly: list[dict], train_weeks: int, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> tuple[dict, dict]:
    def params_of(r: dict) -> tuple[float, float, int]:
        return (r["buy_drop_pct"], r["sell_rise_pct"], r["interval_minutes"])

    app = range(train_weeks, len(weekly))
    app_dfs = [weekly[i]["wk"]["df"] for i in app]

    planes = {"fija-mediana": [], "wf-pico": [], "wf-meseta": [], "oraculo": []}
    for i in app:
        planes["fija-mediana"].append(median_params([weekly[j]["peak"] for j in range(i)]))

        if train_weeks == 1:
            train_results = weekly[i - 1]["results"]
        else:
            train_df = pd.concat(
                [weekly[j]["wk"]["df"] for j in range(i - train_weeks, i)],
                ignore_index=True,
            )
            train_results = run_grid(train_df, intervals, fee_pct, use_pool, buy_amount)

        planes["wf-pico"].append(params_of(select_peak(train_results)))
        planes["wf-meseta"].append(params_of(select_plateau(train_results)[0]))
        planes["oraculo"].append(params_of(weekly[i]["peak"]))

    resultados = {
        nombre: simulate_adaptive(app_dfs, plan, fee_pct, use_pool, buy_amount)
        for nombre, plan in planes.items()
    }
    return resultados, planes


def regret_series(weekly: list[dict], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]:
    rows = []
    for i in range(1, len(weekly)):
        prev = weekly[i - 1]["peak"]
        d, r, m = prev["buy_drop_pct"], prev["sell_rise_pct"], prev["interval_minutes"]
        sub = weekly[i]["wk"]["df"].iloc[::m].reset_index(drop=True)
        applied = simulate(sub, MAX_BUYS, d, r, fee_pct, use_pool, buy_amount, m)
        own = weekly[i]["peak"]["roi"]
        rows.append({
            "label":       weekly[i]["wk"]["label"],
            "own_roi":     own,
            "applied_roi": applied["roi"],
            "regret":      own - applied["roi"],
        })
    return rows
