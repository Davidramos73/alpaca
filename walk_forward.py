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
