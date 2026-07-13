import importlib.util
import os

import pandas as pd


def _load_module():
    path = os.path.join(os.path.dirname(__file__), "optimize.py")
    spec = importlib.util.spec_from_file_location("double_trailing_optimize_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
simulate_double_trailing = mod.simulate_double_trailing


def _df(prices):
    ts = pd.date_range("2026-01-05 09:30", periods=len(prices), freq="1min")
    return pd.DataFrame({"timestamp": ts, "close": [float(p) for p in prices]})


def _run(prices, **kw):
    args = dict(max_buys=10, buy_drop_pct=0.02, sell_rise_pct=0.07, fee_pct=0.0,
                use_pool=True, buy_amount=10_000.0, interval_minutes=1,
                trail_buy_pct=0.01, trail_sell_pct=0.01)
    args.update(kw)
    return simulate_double_trailing(_df(prices), **args)


def test_caida_sin_rebote_no_compra():
    # BUY_INIT a 100; cae a 98 (arma trailing de compra), sigue a 97 y 96 sin
    # rebotar 1% desde el mínimo: no debe comprar de nuevo.
    r = _run([100, 98, 97, 96])
    assert r["buys"] == 1
    assert r["open_positions"] == 1


def test_caida_con_rebote_compra_al_precio_del_rebote():
    # Arma a 98, valley baja a 96, rebota a 97 (>= 96*1.01 = 96.96): compra a 97.
    trades = []
    df = _df([100, 98, 96, 97, 97])
    simulate_double_trailing(df, 10, 0.02, 0.07, 0.0, True, 10_000.0, 1,
                             trail_buy_pct=0.01, trail_sell_pct=0.01,
                             on_trade=trades.append)
    grid_buys = [t for t in trades if t["type"] == "BUY_GRID"]
    assert len(grid_buys) == 1
    assert grid_buys[0]["price"] == 97.0
    # buy_capture = qty * (precio_de_armado 98 - precio_pagado 97) > 0
    qty = grid_buys[0]["qty"]
    assert abs(grid_buys[0]["buy_capture"] - qty * (98.0 - 97.0)) < 1e-9


def test_buy_capture_total_acumula():
    r = _run([100, 98, 96, 97, 97])
    assert r["buys"] == 2
    assert r["trailing_buys"] == 1
    qty = 10_000.0 / 97.0
    assert abs(r["buy_capture_total"] - qty * 1.0) < 1e-6


def test_venta_trailing_heredada():
    # BUY_INIT a 100; sube a 107 (arma venta), pico 110, stop 108.9, vende a 108.
    r = _run([100, 107, 110, 108])
    assert r["sells"] == 1
    assert r["trailing_sells"] == 1
    assert r["open_positions"] == 0


def test_ciclo_completo_contabilidad():
    # Compra init 100, arma compra a 98, valley 96, compra a 97;
    # luego sube: target de venta = 97*1.07 = 103.79 → arma a 104, pico 106,
    # stop 104.94, vende a 104.5 (LIFO: la posición de 97).
    r = _run([100, 98, 96, 97, 104, 106, 104.5])
    assert r["buys"] == 2 and r["sells"] == 1
    qty2 = 10_000.0 / 97.0
    expected_profit_trade = qty2 * 104.5 - 10_000.0
    # equity final = 100000 - 10000(init) - 10000(grid) + qty2*104.5 + qty1*104.5
    qty1 = 10_000.0 / 100.0
    expected_equity = 100_000.0 - 20_000.0 + qty2 * 104.5 + qty1 * 104.5
    assert abs(r["total_equity"] - expected_equity) < 1e-6
    assert expected_profit_trade > 0


def test_trailing_compra_armado_al_final_no_compra():
    r = _run([100, 98, 97, 96, 95])
    assert r["buys"] == 1
    assert r["open_positions"] == 1


import json

regenerate_manifest = mod.regenerate_manifest


def _write_run_json(path, **overrides):
    payload = {"symbol": "TSLA", "date_start": "2026-01-01", "date_end": "2026-06-28"}
    payload.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_manifest_una_entrada_por_corrida(tmp_path):
    d = tmp_path / "TSLA"
    d.mkdir()
    _write_run_json(d / "optimize_TSLA_20260711_120000_equity.json")
    _write_run_json(d / "optimize_TSLA_20260711_150000_equity.json")
    regenerate_manifest(str(tmp_path))
    manifest = json.load(open(tmp_path / "manifest.json"))
    assert [e["run_ts"] for e in manifest] == ["20260711_150000", "20260711_120000"]
    assert manifest[0]["file"] == "TSLA/optimize_TSLA_20260711_150000_equity.json"
    assert manifest[0]["symbol"] == "TSLA"


def test_manifest_ignora_archivos_ajenos(tmp_path):
    d = tmp_path / "TSLA"
    d.mkdir()
    _write_run_json(d / "optimize_TSLA_20260711_120000_equity.json")
    (d / "otracosa.json").write_text("{}")
    regenerate_manifest(str(tmp_path))
    manifest = json.load(open(tmp_path / "manifest.json"))
    assert len(manifest) == 1
