import importlib.util
import os

import pandas as pd


def _load_module():
    path = os.path.join(os.path.dirname(__file__), "optimize.py")
    spec = importlib.util.spec_from_file_location("franjas_optimize_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
simulate_franjas = mod.simulate_franjas
simulate_vanilla = mod.simulate_vanilla


def _df(prices):
    ts = pd.date_range("2026-01-05 09:30", periods=len(prices), freq="1min")
    return pd.DataFrame({"timestamp": ts, "close": [float(p) for p in prices]})


def _run(prices, drops=(0.02, 0.03, 0.04), rises=(0.03, 0.05, 0.07),
         bounds=(0.06, 0.10), on_trade=None):
    return simulate_franjas(_df(prices), max_buys=10, drops=drops, rises=rises,
                            band_bounds=bounds, fee_pct=0.0, use_pool=True,
                            buy_amount=10_000.0, on_trade=on_trade)


def test_franjas_degeneradas_equivale_a_vanilla():
    # Con una sola franja (sin límites) la simulación es el grid vanilla:
    # compra a -2% encadenada y venta a +3% de la última compra.
    prices = [100, 98, 96.03, 99, 98.99, 99.0]
    r = simulate_vanilla(_df(prices), 10, 0.02, 0.03, 0.0)
    assert r["buys"] == 3          # 100, 98, 96.03
    assert r["sells"] == 1         # 96.03 * 1.03 = 98.9109 <= 98.99


def test_drop_se_ensancha_en_franja_profunda():
    # cycle_ref = 100. Compras a 100, 98, 96 (franja 0, drop 2%) y a 94
    # (caída 6% → franja 1). Desde 94 el drop pasa a 3%: target 91.18.
    # 92 NO debe comprar (con drop 2% sí lo hacía: 94*0.98 = 92.12); 91 sí.
    trades = []
    _run([100, 98, 96, 94, 92, 91], on_trade=trades.append)
    buy_prices = [t["price"] for t in trades if t["type"].startswith("BUY")]
    assert buy_prices == [100, 98, 96, 94, 91]
    bands = [t["band"] for t in trades if t["type"].startswith("BUY")]
    assert bands == [0, 0, 0, 1, 1]


def test_rise_propio_de_la_franja_donde_se_compro():
    # La compra a 94 cae en franja 1 → su rise es 5%, no 3%.
    # Tras comprar a 91 (franja 1), el sell target de 91 es 91*1.05 = 95.55:
    # 95 no vende, 95.6 sí.
    trades = []
    _run([100, 98, 96, 94, 92, 91, 95, 95.6], on_trade=trades.append)
    sells = [t for t in trades if t["type"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["price"] == 95.6
    assert sells[0]["buy_price"] == 91.0


def test_cycle_ref_se_resetea_al_vaciarse_el_grid():
    # Primer ciclo: compra a 100 y vende a 103. Segundo ciclo arranca en 103:
    # una caída a 96.9 es -5.9% desde la nueva referencia (franja 0), no
    # -3.1% desde 100. El drop aplicado sigue siendo 2% desde la última compra.
    trades = []
    _run([100, 103, 103, 100.9, 98.8], on_trade=trades.append)
    buys = [t for t in trades if t["type"].startswith("BUY")]
    # 103 → BUY_INIT nuevo ciclo; 100.9 ≤ 103*0.98 = 100.94 → compra franja 0
    # (caída 2% desde ref 103); 98.8 ≤ 100.9*0.98 = 98.882 → compra franja 0
    # (caída 4.1% < 6%).
    assert [t["price"] for t in buys] == [100, 103, 100.9, 98.8]
    assert [t["band"] for t in buys] == [0, 0, 0, 0]


def test_band_buys_cuenta_por_franja():
    r = _run([100, 98, 96, 94, 92, 91])
    assert r["band_buys"] == [3, 2, 0]
    assert r["buys"] == 5
