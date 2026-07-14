import os
import re
import sys
import argparse
import itertools
import json
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ---------------------------------------------------------------------------
# Rangos de búsqueda (máximo 10%)
# ---------------------------------------------------------------------------
BUY_DROP_RANGE     = [r / 100 for r in range(1, 11)]   # 1% … 10%
SELL_RISE_RANGE    = [r / 100 for r in range(1, 11)]   # 1% … 10%

BUY_AMOUNT    = 10_000.0
STARTING_CASH = 100_000.0

LOGS_DIR = "logs"   # cache .pkl, .log y .csv de cada corrida

# ---------------------------------------------------------------------------
# Simulación (misma lógica que backtest.py, sin I/O)
# ---------------------------------------------------------------------------
def simulate(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, on_trade=None, on_bar=None) -> dict:
    # Capital comprometido: lo que la estrategia puede llegar a invertir
    # (más un colchón para fees). El ROI se mide sobre esta base.
    starting_cash = buy_amount * max_buys * (1.0 + fee_pct)
    cash        = starting_cash
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0
    held_qty    = 0.0
    invested    = 0.0  # costo (sin fee) de las posiciones abiertas — "capital expuesto"

    closes     = df["close"].to_numpy(dtype=float)
    timestamps = df["timestamp"].to_list()

    for i in range(len(closes)):
        price     = closes[i]
        timestamp = timestamps[i]

        if len(purchases) == 0:
            free_slots    = max_buys - len(purchases)
            bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
            effective_buy = buy_amount + bonus
            buy_fee = effective_buy * fee_pct
            if cash >= effective_buy + buy_fee:
                qty     = effective_buy / price
                cash   -= effective_buy + buy_fee
                held_qty += qty
                invested += effective_buy
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
            if on_bar:
                on_bar(timestamp, cash + held_qty * price, invested)
            continue

        last_price  = purchases[-1]["price"]
        buy_target  = last_price * (1.0 - buy_drop_pct)
        sell_target = last_price * (1.0 + sell_rise_pct)

        if price <= buy_target:
            if len(purchases) < max_buys:
                free_slots    = max_buys - len(purchases)
                bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
                effective_buy = buy_amount + bonus
                buy_fee = effective_buy * fee_pct
                if cash < effective_buy + buy_fee:
                    if on_bar:
                        on_bar(timestamp, cash + held_qty * price, invested)
                    continue
                qty     = effective_buy / price
                cash   -= effective_buy + buy_fee
                held_qty += qty
                invested += effective_buy
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
                          "open_positions": len(purchases),
                          "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                          "order_id": sold["order_id"]})

        if on_bar:
            on_bar(timestamp, cash + held_qty * price, invested)

    final_price    = float(closes[-1])
    holdings_value = sum(p["qty"] for p in purchases) * final_price
    total_equity   = cash + holdings_value
    profit         = total_equity - starting_cash
    roi            = (profit / starting_cash) * 100

    return {
        "interval_minutes": interval_minutes,
        "max_buys":       max_buys,
        "buy_drop_pct":   buy_drop_pct,
        "sell_rise_pct":  sell_rise_pct,
        "fee_pct":        fee_pct,
        "starting_cash":  starting_cash,
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
    starting_cash = buy_amount * max_buys * (1.0 + fee_pct)
    cash        = starting_cash
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
    profit         = total_equity - starting_cash
    roi            = (profit / starting_cash) * 100

    return {
        "interval_minutes": interval_minutes,
        "max_buys":       max_buys,
        "buy_drop_pct":   buy_drop_pct,
        "sell_rise_pct":  sell_rise_pct,
        "fee_pct":        fee_pct,
        "trail_pct":      trail_pct,
        "starting_cash":  starting_cash,
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

def simulate_double_trailing(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, trail_buy_pct: float = 0.0, trail_sell_pct: float = 0.0, on_trade=None, on_bar=None) -> dict:
    """Trailing en ambas puntas. Compra inicial inmediata. Compra de grid: al
    caer buy_drop_pct desde la última compra se arma un trailing de compra que
    sigue el mínimo vela a vela y compra recién cuando el precio rebota
    trail_buy_pct desde ese mínimo. Venta: idéntica a simulate_trailing()
    (pico + retroceso trail_sell_pct). Mientras cualquiera de los dos
    trailings está armado no se evalúan los gatillos del grid.
    buy_capture (por compra y total) compara el precio pagado contra el
    precio al que se armó el trailing (lo que hubiera pagado la versión sin
    trailing de compra). df debe ser histórico de 1 minuto completo."""
    starting_cash = buy_amount * max_buys * (1.0 + fee_pct)
    cash        = starting_cash
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0
    held_qty    = 0.0
    invested    = 0.0  # costo (sin fee) de las posiciones abiertas — "capital expuesto"
    trailing_capture_total = 0.0
    trailing_sells = 0
    buy_capture_total = 0.0
    trailing_buys = 0

    trailing_sell = None  # {"peak", "stop", "sell_target_ref"}
    trailing_buy  = None  # {"valley", "arm", "arm_ref"}

    def _do_buy(price, timestamp, ev_type, buy_capture=None):
        nonlocal cash, held_qty, invested, profit_pool, total_fees, total_buys, buy_capture_total, trailing_buys
        free_slots    = max_buys - len(purchases)
        bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
        effective_buy = buy_amount + bonus
        buy_fee = effective_buy * fee_pct
        if cash < effective_buy + buy_fee:
            return
        qty     = effective_buy / price
        cash   -= effective_buy + buy_fee
        held_qty += qty
        invested += effective_buy
        if use_pool:
            profit_pool -= bonus
        total_fees += buy_fee
        total_buys += 1
        order_id = total_buys
        purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                          "timestamp": timestamp, "order_id": order_id})
        ev = {"type": ev_type, "price": price, "qty": qty, "fee": buy_fee,
              "cash": cash, "pool": profit_pool, "timestamp": timestamp,
              "open_positions": len(purchases), "order_id": order_id}
        if buy_capture is not None:
            capture = qty * buy_capture
            buy_capture_total += capture
            trailing_buys += 1
            ev["buy_capture"] = capture
        if on_trade:
            on_trade(ev)

    closes     = df["close"].to_numpy(dtype=float)
    timestamps = df["timestamp"].to_list()

    for i in range(len(closes)):
        price     = closes[i]
        timestamp = timestamps[i]

        if trailing_sell is not None:
            if price > trailing_sell["peak"]:
                trailing_sell["peak"] = price
                trailing_sell["stop"] = trailing_sell["peak"] * (1.0 - trail_sell_pct)
            if price <= trailing_sell["stop"]:
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
                capture = sold["qty"] * (price - trailing_sell["sell_target_ref"])
                trailing_capture_total += capture
                trailing_sells += 1
                if on_trade:
                    on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                              "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                              "open_positions": len(purchases),
                              "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                              "order_id": sold["order_id"], "trailing_capture": capture})
                trailing_sell = None
            if on_bar:
                on_bar(timestamp, cash + held_qty * price, invested)
            continue

        if trailing_buy is not None:
            if price < trailing_buy["valley"]:
                trailing_buy["valley"] = price
                trailing_buy["arm"]    = trailing_buy["valley"] * (1.0 + trail_buy_pct)
            if price >= trailing_buy["arm"]:
                if len(purchases) < max_buys:
                    _do_buy(price, timestamp, "BUY_GRID",
                            buy_capture=trailing_buy["arm_ref"] - price)
                trailing_buy = None
            if on_bar:
                on_bar(timestamp, cash + held_qty * price, invested)
            continue

        if i % interval_minutes != 0:
            if on_bar:
                on_bar(timestamp, cash + held_qty * price, invested)
            continue

        if len(purchases) == 0:
            _do_buy(price, timestamp, "BUY_INIT")
        else:
            last_price  = purchases[-1]["price"]
            buy_target  = last_price * (1.0 - buy_drop_pct)
            sell_target = last_price * (1.0 + sell_rise_pct)

            if price <= buy_target:
                if len(purchases) < max_buys:
                    trailing_buy = {"valley": price, "arm": price * (1.0 + trail_buy_pct), "arm_ref": price}
            elif price >= sell_target:
                trailing_sell = {"peak": price, "stop": price * (1.0 - trail_sell_pct), "sell_target_ref": sell_target}

        if on_bar:
            on_bar(timestamp, cash + held_qty * price, invested)

    # Fin de datos con trailing de venta activo: liquidar al último close.
    # (Con trailing de compra activo simplemente no se compra.)
    if trailing_sell is not None and purchases:
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
        capture = sold["qty"] * (price - trailing_sell["sell_target_ref"])
        trailing_capture_total += capture
        trailing_sells += 1
        if on_trade:
            on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                      "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                      "open_positions": len(purchases),
                      "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                      "order_id": sold["order_id"], "trailing_capture": capture})

    final_price    = float(closes[-1])
    holdings_value = sum(p["qty"] for p in purchases) * final_price
    total_equity   = cash + holdings_value
    profit         = total_equity - starting_cash
    roi            = (profit / starting_cash) * 100

    return {
        "interval_minutes": interval_minutes,
        "max_buys":        max_buys,
        "buy_drop_pct":    buy_drop_pct,
        "sell_rise_pct":   sell_rise_pct,
        "fee_pct":         fee_pct,
        "starting_cash":   starting_cash,
        "trail_buy_pct":   trail_buy_pct,
        "trail_sell_pct":  trail_sell_pct,
        "roi":             roi,
        "profit":          profit,
        "total_equity":    total_equity,
        "total_fees":      total_fees,
        "buys":            total_buys,
        "sells":           total_sells,
        "open_positions":  len(purchases),
        "trailing_capture_total": trailing_capture_total,
        "trailing_sells":         trailing_sells,
        "buy_capture_total":      buy_capture_total,
        "trailing_buys":          trailing_buys,
    }

# ---------------------------------------------------------------------------
# Grid en paralelo: cada proceso recibe el histórico una sola vez (initializer)
# y resuelve combos independientes.
# ---------------------------------------------------------------------------
_WORKER_DF = None

def _init_worker(df_1min):
    global _WORKER_DF
    _WORKER_DF = df_1min

def _run_vanilla_combo(job):
    buy_drop, sell_rise, max_buys, fee_pct, use_pool, buy_amount = job
    return simulate(_WORKER_DF, max_buys, buy_drop, sell_rise, fee_pct, use_pool, buy_amount, 1)

def _run_dt_combo(job):
    buy_drop, sell_rise, tb, tsell, max_buys, fee_pct, use_pool, buy_amount = job
    return simulate_double_trailing(_WORKER_DF, max_buys, buy_drop, sell_rise, fee_pct,
                                     use_pool, buy_amount, 1, trail_buy_pct=tb, trail_sell_pct=tsell)


def daily_last(records: list[tuple]) -> list[dict]:
    """Reduce una serie (timestamp, valor) a un punto por día de calendario
    (el último valor visto ese día, que con datos intradía ordenados es el
    más cercano al cierre de mercado)."""
    daily: dict = {}
    for ts, value in records:
        daily[ts.date()] = value
    return [{"date": d.isoformat(), "value": v} for d, v in sorted(daily.items())]

def daily_last_exposure(records: list[tuple]) -> list[dict]:
    """Como daily_last(), pero reduce (timestamp, equity, exposure) a un punto
    diario con ambos campos — exposure es el costo (sin fee) de las
    posiciones abiertas en ese momento, i.e. cuánto del budget está
    efectivamente invertido en el mercado."""
    daily: dict = {}
    for ts, equity, exposure in records:
        daily[ts.date()] = (equity, exposure)
    return [{"date": d.isoformat(), "equity": v[0], "exposure": v[1]} for d, v in sorted(daily.items())]

RUN_EQUITY_RE = re.compile(r"^optimize_(?P<symbol>[^_]+)_(?P<run_ts>\d{8}_\d{6})_equity\.json$")

def regenerate_manifest(out_dir: str) -> str:
    """Escanea out_dir/<símbolo>/ y regenera manifest.json con una entrada
    por corrida (a diferencia de trailing, acá cada corrida es un solo JSON)."""
    entries = []
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            m = RUN_EQUITY_RE.match(name)
            if not m:
                continue
            path = os.path.join(root, name)
            rel  = os.path.relpath(path, out_dir).replace(os.sep, "/")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            entries.append({
                "symbol":     payload.get("symbol", m.group("symbol")),
                "run_ts":     m.group("run_ts"),
                "date_start": payload.get("date_start"),
                "date_end":   payload.get("date_end"),
                "file":       rel,
            })
    entries.sort(key=lambda e: e["run_ts"], reverse=True)
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    return manifest_path

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _capture_callbacks(trades_out, bars_out):
    """Devuelve (on_trade, on_bar) que registran trades en formato del viewer
    y equity por barra para reducir a diario."""
    def on_bar(ts, eq, exposure):
        bars_out.append((ts, eq, exposure))

    def on_trade(ev):
        trade = {
            "type":     ev["type"],
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
        if "buy_capture" in ev:
            trade["buy_capture"] = ev["buy_capture"]
        trades_out.append(trade)

    return on_trade, on_bar

def main():
    parser = argparse.ArgumentParser(description="Optimizador double trailing (compra y venta con trailing)")
    parser.add_argument("--symbol",     type=str,   default="TSLA",       help="Símbolo a analizar (default: TSLA)")
    parser.add_argument("--date-start", type=str,   default="2026-01-01", help="Fecha inicio YYYY-MM-DD (default: 2026-01-01)")
    parser.add_argument("--date-end",   type=str,   default="2026-06-28", help="Fecha fin YYYY-MM-DD (default: 2026-06-28)")
    parser.add_argument("--buy-amount", type=float, default=10_000.0,     help="Monto base por compra en USD (default: 10000)")
    parser.add_argument("--max-buys",   type=int,   default=10,           help="Máximo de compras activas simultáneas (default: 10)")
    parser.add_argument("--fee-pct",    type=float, default=0.0,          help="Fee por operación sobre el monto (default: 0.0). Ej: 0.001 = 0.1%%")
    parser.add_argument("--no-profit-pool", action="store_true",          help="Desactivar reinversión de ganancias (modo clásico)")
    parser.add_argument("--trail-buy-pcts",  type=str, default="0.5,1,1.5", help="Lista de %% de rebote para el trailing de compra, separados por coma (default: 0.5,1,1.5)")
    parser.add_argument("--trail-sell-pcts", type=str, default="0.5,1,1.5", help="Lista de %% de retroceso para el trailing de venta, separados por coma (default: 0.5,1,1.5)")
    parser.add_argument("--export-equity-json", action="store_true", help="Exportar JSON con top combos + referencia vanilla para el visor React")
    parser.add_argument("--out-dir", type=str, default="viewer/public/data", help="Carpeta base del JSON para el visor, organizado en out-dir/<símbolo>/ (default: viewer/public/data)")
    args = parser.parse_args()

    def _parse_pcts(raw, flag):
        try:
            vals = sorted({float(v.strip()) / 100 for v in raw.split(",") if v.strip()})
        except ValueError:
            print(f"Error: {flag} inválido ({raw}); usá números separados por coma, ej. 0.5,1,1.5")
            sys.exit(1)
        if not vals or any(v <= 0 for v in vals):
            print(f"Error: {flag} requiere valores positivos, ej. 0.5,1,1.5")
            sys.exit(1)
        return vals

    trail_buy_pcts  = _parse_pcts(args.trail_buy_pcts,  "--trail-buy-pcts")
    trail_sell_pcts = _parse_pcts(args.trail_sell_pcts, "--trail-sell-pcts")

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
              f"Verificá el símbolo y el rango de fechas.")
        sys.exit(1)

    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    fee_pct    = args.fee_pct
    buy_amount = args.buy_amount
    max_buys   = args.max_buys
    use_pool   = not args.no_profit_pool

    workers = os.cpu_count() or 1
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(df_1min,)) as executor:
        # --- Referencia vanilla: grilla 2D drop x rise a intervalo 1 min ---
        vanilla_combos = list(itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE))
        print(f"Fase vanilla: {len(vanilla_combos)} combinaciones drop/rise (referencia)…")
        vanilla_jobs = [(buy_drop, sell_rise, max_buys, fee_pct, use_pool, buy_amount)
                        for buy_drop, sell_rise in vanilla_combos]
        vanilla_results = list(executor.map(_run_vanilla_combo, vanilla_jobs, chunksize=2))
        vanilla_results.sort(key=lambda r: r["roi"], reverse=True)
        vanilla_best = vanilla_results[0]

        # --- Grilla 4D double trailing ---
        combos = list(itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE, trail_buy_pcts, trail_sell_pcts))
        total  = len(combos)
        print(f"Grilla double trailing: {total} combinaciones "
              f"(drop x rise x trail_buy {[p*100 for p in trail_buy_pcts]} x trail_sell {[p*100 for p in trail_sell_pcts]}; "
              f"max_buys = {max_buys}, buy_amount = ${buy_amount:,.0f}, fee = {fee_pct*100:.3f}%, pool = {'ON' if use_pool else 'OFF'})…\n")

        dt_jobs = [(buy_drop, sell_rise, tb, tsell, max_buys, fee_pct, use_pool, buy_amount)
                   for buy_drop, sell_rise, tb, tsell in combos]
        results = []
        for done, r in enumerate(executor.map(_run_dt_combo, dt_jobs, chunksize=2), 1):
            if done % 10 == 0 or done == total:
                print(f"  {done}/{total}", end="\r")
            results.append(r)
        results.sort(key=lambda r: r["roi"], reverse=True)

    top_n    = 20
    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.log")
    csv_path = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.csv")

    periodo_start = df_1min.iloc[0]["timestamp"].strftime("%Y-%m-%d")
    periodo_end   = df_1min.iloc[-1]["timestamp"].strftime("%Y-%m-%d")
    best  = results[0]
    worst = results[-1]

    sep  = "=" * 96
    sep2 = "-" * 96
    header_row = (
        f"{'#':>3}  {'drop%':>6}  {'rise%':>6}  {'t.buy%':>7}  {'t.sell%':>8}  {'ROI%':>8}  "
        f"{'Ganancia':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'BuyCapt':>10}  {'SellCapt':>10}"
    )

    def fmt_row(rank, r):
        return (
            f"{rank:>3}  "
            f"{r['buy_drop_pct']*100:>5.0f}%  "
            f"{r['sell_rise_pct']*100:>5.0f}%  "
            f"{r['trail_buy_pct']*100:>6.1f}%  "
            f"{r['trail_sell_pct']*100:>7.1f}%  "
            f"{r['roi']:>+8.2f}%  "
            f"${r['profit']:>+11,.0f}  "
            f"{r['buys']:>7}  "
            f"{r['sells']:>6}  "
            f"{r['open_positions']:>5}  "
            f"${r['buy_capture_total']:>+9,.0f}  "
            f"${r['trailing_capture_total']:>+9,.0f}"
        )

    lines = [
        sep,
        f"  OPTIMIZE DOUBLE TRAILING {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        f"  Período analizado:  {periodo_start}  →  {periodo_end}",
        f"  Velas de 1 minuto:  {len(df_1min)}   |   Intervalo fijo: 1 min",
        f"  Capital comprometido: ${buy_amount * max_buys * (1.0 + fee_pct):,.2f}   |   Monto por compra: ${buy_amount:,.2f}",
        f"  max_buys:           {max_buys}",
        f"  Combinaciones evaluadas: {total} (double trailing) + {len(vanilla_combos)} (vanilla referencia)",
        sep2,
        f"  REFERENCIA VANILLA (mejor drop/rise sin trailing)",
        f"  drop {vanilla_best['buy_drop_pct']*100:.0f}% / rise {vanilla_best['sell_rise_pct']*100:.0f}%  "
        f"ROI {vanilla_best['roi']:+.2f}%  Ganancia ${vanilla_best['profit']:+,.0f}  "
        f"Compras {vanilla_best['buys']}  Ventas {vanilla_best['sells']}",
        sep2,
        "",
        f"  TOP {top_n} COMBINACIONES DOUBLE TRAILING (ordenadas por ROI)",
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

    if args.export_equity_json:
        symbol_dir = os.path.join(args.out_dir, symbol)
        os.makedirs(symbol_dir, exist_ok=True)
        price_daily = daily_last(zip(df_1min["timestamp"], df_1min["close"].astype(float)))

        # Series de la referencia vanilla (re-corre el mejor combo con callbacks)
        v_trades, v_bars = [], []
        on_trade, on_bar = _capture_callbacks(v_trades, v_bars)
        simulate(df_1min, max_buys, vanilla_best["buy_drop_pct"], vanilla_best["sell_rise_pct"],
                 fee_pct, use_pool, buy_amount, 1, on_trade=on_trade, on_bar=on_bar)
        vanilla_payload = {
            "drop_pct": round(vanilla_best["buy_drop_pct"] * 100),
            "rise_pct": round(vanilla_best["sell_rise_pct"] * 100),
            "roi":      vanilla_best["roi"],
            "profit":   vanilla_best["profit"],
            "buys":     vanilla_best["buys"],
            "sells":    vanilla_best["sells"],
            "equity":   daily_last_exposure(v_bars),
            "trades":   v_trades,
        }

        # Series de cada combo del top N (re-corre con callbacks)
        combos_payload = []
        print(f"Generando series del top {top_n}…")
        for r in results[:top_n]:
            c_trades, c_bars = [], []
            on_trade, on_bar = _capture_callbacks(c_trades, c_bars)
            simulate_double_trailing(df_1min, max_buys, r["buy_drop_pct"], r["sell_rise_pct"], fee_pct,
                                     use_pool, buy_amount, 1,
                                     trail_buy_pct=r["trail_buy_pct"], trail_sell_pct=r["trail_sell_pct"],
                                     on_trade=on_trade, on_bar=on_bar)
            combos_payload.append({
                "drop_pct":       round(r["buy_drop_pct"] * 100),
                "rise_pct":       round(r["sell_rise_pct"] * 100),
                "trail_buy_pct":  r["trail_buy_pct"] * 100,
                "trail_sell_pct": r["trail_sell_pct"] * 100,
                "roi":            r["roi"],
                "profit":         r["profit"],
                "buys":           r["buys"],
                "sells":          r["sells"],
                "open_positions": r["open_positions"],
                "buy_capture_total":      r["buy_capture_total"],
                "trailing_capture_total": r["trailing_capture_total"],
                "equity": daily_last_exposure(c_bars),
                "trades": c_trades,
            })

        payload = {
            "symbol":        symbol,
            "date_start":    periodo_start,
            "date_end":      periodo_end,
            "starting_cash": buy_amount * max_buys * (1.0 + fee_pct),
            "price":         [{"date": p["date"], "close": p["value"]} for p in price_daily],
            "vanilla":       vanilla_payload,
            "combos":        combos_payload,
        }
        equity_json_path = os.path.join(symbol_dir, f"optimize_{symbol}_{run_ts}_equity.json")
        with open(equity_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"JSON del visor   : {equity_json_path}")
        manifest_path = regenerate_manifest(args.out_dir)
        print(f"Manifest visor   : {manifest_path}")

if __name__ == "__main__":
    main()
