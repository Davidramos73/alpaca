import pandas as pd
import pytest

from optimize import simulate, new_state, MAX_BUYS
from walk_forward import split_weeks


def make_df(prices, start="2026-01-05 14:30", freq="1min"):
    ts = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    return pd.DataFrame({"timestamp": ts, "close": [float(p) for p in prices]})


# Secuencia calculada a mano (drop=5%, rise=5%, fee=0, pool ON, buy=10000):
#   100 -> compra inicial (100 acciones, cash 90000)
#   94  -> <= 95 (target compra): compra 10000/94 acciones, cash 80000
#   99  -> >= 98.7 (target venta s/94): vende, revenue 10531.9148936..., pool 531.91...
#   106 -> >= 105 (target venta s/100): vende 100 acc., revenue 10600, pool 1131.9148936...
#   100 -> sin posiciones: compra con bonus pool/10 = 113.19..., qty 101.1319...
ZIGZAG = [100, 94, 99, 106, 100]


def test_simulate_retrocompatible_zigzag():
    df = make_df(ZIGZAG)
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert r["buys"] == 3
    assert r["sells"] == 2
    assert r["open_positions"] == 1
    assert r["total_equity"] == pytest.approx(101131.9148936, abs=1e-4)
    assert r["roi"] == pytest.approx(1.1319148936, abs=1e-6)
    assert "state" in r


def test_simulate_encadenado_equivale_a_corrida_unica():
    df = make_df(ZIGZAG)
    completo = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)

    df_a, df_b = df.iloc[:3].reset_index(drop=True), df.iloc[3:].reset_index(drop=True)
    r1 = simulate(df_a, MAX_BUYS, 0.05, 0.05, 0.0)
    r2 = simulate(df_b, MAX_BUYS, 0.05, 0.05, 0.0, state=r1["state"])

    assert r2["total_equity"] == pytest.approx(completo["total_equity"])
    assert r2["roi"] == pytest.approx(completo["roi"])
    assert r2["buys"] == completo["buys"]
    assert r2["sells"] == completo["sells"]
    assert r2["open_positions"] == completo["open_positions"]


def test_simulate_no_muta_el_state_del_caller():
    df = make_df(ZIGZAG)
    st = new_state()
    simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, state=st)
    assert st == new_state()


def test_simulate_usa_buy_amount():
    df = make_df([100.0])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, buy_amount=5_000.0)
    assert r["state"]["cash"] == pytest.approx(95_000.0)


def test_split_weeks_semanas_iso_y_huecos():
    # Vie 2026-01-09 (W02), Lun/Mar 2026-01-12/13 (W03), hueco, Mié 2026-01-21 (W04)
    partes = [
        make_df([100] * 10, start="2026-01-09 15:00"),
        make_df([100] * 20, start="2026-01-12 15:00"),
        make_df([100] * 15, start="2026-01-13 15:00"),
        make_df([100] * 5,  start="2026-01-21 15:00"),
    ]
    df = pd.concat(partes, ignore_index=True)
    weeks = split_weeks(df)

    assert [w["label"] for w in weeks] == ["2026-W02", "2026-W03", "2026-W04"]
    assert [len(w["df"]) for w in weeks] == [10, 35, 5]
    assert weeks[0]["start"] == df["timestamp"].iloc[0]
    assert weeks[2]["end"] == df["timestamp"].iloc[-1]
    # cada df semanal viene con índice reseteado
    assert list(weeks[1]["df"].index) == list(range(35))
