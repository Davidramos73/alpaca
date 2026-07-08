import pytest
import pandas as pd

from optimize import simulate_trailing


def make_df(prices):
    idx = pd.date_range("2026-01-01 09:30", periods=len(prices), freq="1min")
    return pd.DataFrame({"timestamp": idx, "close": prices})


def test_trailing_rides_and_sells_on_pullback():
    """Compra en 100. Al checkpoint siguiente el precio llegó a 105 (rise 5%),
    en vez de vender arma el trailing. Sube a 108 y 110 (el stop sube con
    el pico), y al retroceder a 107 vende ahí — por encima de los 105 que
    hubiera vendido la versión vanilla."""
    df = make_df([100, 101, 105, 108, 110, 107])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=2, trail_pct=0.02,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "SELL"]
    sell = trades[1]
    assert sell["price"] == pytest.approx(107.0)
    assert sell["buy_price"] == pytest.approx(100.0)
    assert sell["profit"] == pytest.approx(700.0)
    assert sell["trailing_capture"] == pytest.approx(200.0)

    assert result["trailing_sells"] == 1
    assert result["trailing_capture_total"] == pytest.approx(200.0)
    assert result["roi"] == pytest.approx(0.7)
    assert result["open_positions"] == 0


def test_returns_same_shape_as_simulate_plus_trailing_fields():
    df = make_df([100, 101])
    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=True, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
    )
    expected_keys = {
        "interval_minutes", "max_buys", "buy_drop_pct", "sell_rise_pct", "fee_pct", "trail_pct",
        "roi", "profit", "total_equity", "total_fees", "buys", "sells", "open_positions",
        "trailing_capture_total", "trailing_sells", "trailing_captures",
    }
    assert set(result.keys()) == expected_keys


def test_trailing_capture_is_negative_on_immediate_pullback():
    """Arma el trailing en 105 (rise 5% sobre compra en 100) y en la vela
    siguiente el precio ya cayó a 103, por debajo del stop (103.95) — vende
    ahí. La ganancia real sigue siendo positiva (103 > 100), pero el
    trailing_capture es negativo porque vendió por debajo de los 105 que
    hubiera vendido la versión vanilla."""
    df = make_df([100, 105, 103])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    sell = trades[1]
    assert sell["price"] == pytest.approx(103.0)
    assert sell["profit"] == pytest.approx(300.0)
    assert sell["trailing_capture"] == pytest.approx(-200.0)
    assert result["trailing_capture_total"] == pytest.approx(-200.0)


def test_trailing_liquidates_at_last_close_if_never_triggered():
    """El precio sube monótono hasta el final de los datos sin retroceder
    lo suficiente para disparar el stop — se liquida al último close
    disponible en vez de quedar con una posición fantasma."""
    df = make_df([100, 105, 108, 112])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "SELL"]
    sell = trades[1]
    assert sell["price"] == pytest.approx(112.0)
    assert result["trailing_sells"] == 1
    assert result["trailing_capture_total"] == pytest.approx(700.0)
    assert result["open_positions"] == 0


def test_restarts_with_new_buy_after_trailing_empties_the_stack():
    """Tras vender el único lote por el trailing, el próximo checkpoint
    arranca un ciclo nuevo con una compra inicial — el grid no queda
    trabado con la pila vacía."""
    df = make_df([100, 105, 103, 90])
    trades = []

    simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "SELL", "BUY_INIT"]
    assert trades[2]["price"] == pytest.approx(90.0)


def test_holds_lower_lot_untouched_and_reverts_reference_after_trailing_sell():
    """Dos lotes abiertos (100 y 95, tope=95). El tope arma trailing al
    llegar a 99.75 (rise 5%), sube a 105 sin vender NADA (ni el tope
    -todavía en trailing- ni el lote de abajo), y al retroceder a 103
    vende el tope (95) — no el de 100. Después, la referencia para la
    siguiente decisión vuelve correctamente al lote restante (100), no al
    precio de venta (103) ni al sell_target que armó el trailing (99.75):
    la compra grid siguiente se dispara en 94 porque 94 <= 100*(1-0.05)=95."""
    df = make_df([100, 95, 99.75, 105, 103, 94])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "BUY_GRID", "SELL", "BUY_GRID"]
    assert trades[0]["price"] == pytest.approx(100.0)
    assert trades[1]["price"] == pytest.approx(95.0)

    sell = trades[2]
    assert sell["price"] == pytest.approx(103.0)
    assert sell["buy_price"] == pytest.approx(95.0)  # vendió el TOPE (95), no el de 100

    assert trades[3]["price"] == pytest.approx(94.0)  # referencia volvió al lote de 100

    assert result["open_positions"] == 2  # quedan el de 100 y el nuevo de 94
