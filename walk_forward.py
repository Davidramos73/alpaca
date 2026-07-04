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
