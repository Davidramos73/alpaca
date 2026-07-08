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
