import numpy as np
import pandas as pd
import pytest

from optimize import simulate, new_state, MAX_BUYS
from walk_forward import (
    lag1_corr,
    median_params,
    regret_series,
    run_grid,
    select_peak,
    select_plateau,
    split_weeks,
)


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


def _combo(drop_pp, rise_pp, roi, interval=20):
    return {
        "interval_minutes": interval,
        "buy_drop_pct":  drop_pp / 100,
        "sell_rise_pct": rise_pp / 100,
        "roi": roi,
    }


def test_select_plateau_prefiere_meseta_sobre_pico_aislado():
    # Grid 10x10: base 0, meseta de 5.0 en drop/rise 2..4, pico aislado de 10.0 en (9,9)
    results = []
    for d in range(1, 11):
        for r in range(1, 11):
            if 2 <= d <= 4 and 2 <= r <= 4:
                roi = 5.0
            elif d == 9 and r == 9:
                roi = 10.0
            else:
                roi = 0.0
            results.append(_combo(d, r, roi))

    assert select_peak(results)["buy_drop_pct"] == pytest.approx(0.09)

    plateau, score = select_plateau(results)
    assert plateau["buy_drop_pct"] == pytest.approx(0.03)
    assert plateau["sell_rise_pct"] == pytest.approx(0.03)
    assert score == pytest.approx(5.0)   # los 9 vecinos de (3,3) valen 5.0


def test_select_plateau_no_mezcla_intervalos():
    # Mismo drop/rise, distinto intervalo: los vecindarios son independientes
    results = [_combo(3, 3, 5.0, interval=20), _combo(3, 3, 9.0, interval=60)]
    plateau, score = select_plateau(results)
    assert plateau["interval_minutes"] == 60
    assert score == pytest.approx(9.0)


def test_run_grid_dimensiones_y_mejor_roi():
    # 60 velas de random walk determinístico
    rng = np.random.default_rng(42)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 60))
    df = make_df(prices)

    results = run_grid(df, [1, 5], 0.0, True, 10_000.0)
    assert len(results) == 100 * 2          # 10 drops x 10 rises x 2 intervalos
    peak = select_peak(results)
    assert peak["roi"] == max(r["roi"] for r in results)


def test_lag1_corr():
    assert lag1_corr([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert np.isnan(lag1_corr([5, 5, 5, 5]))      # varianza cero
    assert np.isnan(lag1_corr([1.0, 2.0]))        # muestra insuficiente


def test_median_params():
    peaks = [
        _combo(3, 2, 1.0, interval=20),
        _combo(5, 6, 1.0, interval=20),
        _combo(4, 10, 1.0, interval=5),
    ]
    drop, rise, interval = median_params(peaks)
    assert drop == pytest.approx(0.04)
    assert rise == pytest.approx(0.06)
    assert interval == 20


def test_median_params_conteo_par_redondeo_banker():
    # 4 picos (conteo par) -> np.median promedia los dos valores centrales de
    # buy_drop_pct = [2,3,6,7] pp, dando exactamente 4.5pp: un empate exacto
    # de redondeo. round() de Python usa "banker's rounding" (redondeo al par
    # más cercano), por lo que round(4.5) -> 4, no 5. Este test fija ese
    # comportamiento real y no obvio para que un caller futuro (el torneo de
    # la Task 6, que invocará median_params con conteos pares de picos
    # semanales pasados) no se sorprenda si algún día cambia.
    peaks = [
        _combo(2, 10, 1.0, interval=20),
        _combo(3, 10, 1.0, interval=20),
        _combo(6, 10, 1.0, interval=5),
        _combo(7, 10, 1.0, interval=20),
    ]
    drop, rise, interval = median_params(peaks)
    assert drop == pytest.approx(0.04)  # mediana 4.5pp -> round-half-to-even -> 4
    assert rise == pytest.approx(0.10)
    assert interval == 20


def _weekly_sintetico(n_semanas=2, bars=80):
    rng = np.random.default_rng(7)
    weekly = []
    lunes = pd.date_range("2026-01-05", periods=n_semanas, freq="7D")
    for i in range(n_semanas):
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, bars))
        df = make_df(prices, start=lunes[i].strftime("%Y-%m-%d 15:00"))
        results = run_grid(df, [1], 0.0, True, 10_000.0)
        plateau, score = select_plateau(results)
        weekly.append({
            "wk": {"label": f"2026-W{2 + i:02d}", "start": df["timestamp"].iloc[0],
                   "end": df["timestamp"].iloc[-1], "df": df},
            "results": results,
            "peak": select_peak(results),
            "plateau": plateau,
            "plateau_score": score,
        })
    return weekly


def test_regret_nunca_negativo():
    # El combo aplicado sale del mismo grid que define el óptimo propio,
    # así que own_roi >= applied_roi siempre.
    weekly = _weekly_sintetico()
    rows = regret_series(weekly, 0.0, True, 10_000.0)
    assert len(rows) == 1
    assert rows[0]["label"] == "2026-W03"
    assert rows[0]["own_roi"] == pytest.approx(weekly[1]["peak"]["roi"])
    assert rows[0]["regret"] >= -1e-9
