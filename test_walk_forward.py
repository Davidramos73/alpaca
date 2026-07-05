import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from optimize import simulate, new_state, MAX_BUYS
from walk_forward import (
    lag1_corr,
    median_params,
    regret_series,
    run_analysis,
    run_grid,
    select_peak,
    select_plateau,
    simulate_adaptive,
    split_periods,
    tournament,
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


def test_split_periods_semanas_iso_y_huecos():
    # Vie 2026-01-09 (W02), Lun/Mar 2026-01-12/13 (W03), hueco, Mié 2026-01-21 (W04)
    partes = [
        make_df([100] * 10, start="2026-01-09 15:00"),
        make_df([100] * 20, start="2026-01-12 15:00"),
        make_df([100] * 15, start="2026-01-13 15:00"),
        make_df([100] * 5,  start="2026-01-21 15:00"),
    ]
    df = pd.concat(partes, ignore_index=True)
    periods = split_periods(df, "week")

    assert [p["label"] for p in periods] == ["2026-W02", "2026-W03", "2026-W04"]
    assert [len(p["df"]) for p in periods] == [10, 35, 5]
    assert periods[0]["start"] == df["timestamp"].iloc[0]
    assert periods[2]["end"] == df["timestamp"].iloc[-1]
    # cada df de período viene con índice reseteado
    assert list(periods[1]["df"].index) == list(range(35))


def test_split_periods_meses_calendario_y_hueco():
    # Mié 2026-01-28 y Vie 2026-01-30 (mismo mes, M01), hueco de una semana,
    # Mié 2026-02-11 (M02)
    partes = [
        make_df([100] * 10, start="2026-01-28 15:00"),
        make_df([100] * 8,  start="2026-01-30 15:00"),
        make_df([100] * 5,  start="2026-02-11 15:00"),
    ]
    df = pd.concat(partes, ignore_index=True)
    periods = split_periods(df, "month")

    assert [p["label"] for p in periods] == ["2026-M01", "2026-M02"]
    assert [len(p["df"]) for p in periods] == [18, 5]
    assert periods[0]["start"] == df["timestamp"].iloc[0]
    assert periods[1]["end"] == df["timestamp"].iloc[-1]
    assert list(periods[0]["df"].index) == list(range(18))


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


def test_simulate_adaptive_params_constantes_equivale_a_simulate():
    rng = np.random.default_rng(3)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 120))
    df = make_df(prices)
    semanas = [df.iloc[:40].reset_index(drop=True),
               df.iloc[40:90].reset_index(drop=True),
               df.iloc[90:].reset_index(drop=True)]

    completo = simulate(df, MAX_BUYS, 0.03, 0.02, 0.0)
    adaptado = simulate_adaptive(semanas, [(0.03, 0.02, 1)] * 3, 0.0, True, 10_000.0)

    assert adaptado["total_equity"] == pytest.approx(completo["total_equity"])
    assert adaptado["buys"] == completo["buys"]
    assert adaptado["sells"] == completo["sells"]


def test_tournament_estructura_y_semanas_de_aplicacion():
    weekly = _weekly_sintetico(n_semanas=3)
    resultados, planes = tournament(weekly, 1, [1], 0.0, True, 10_000.0)

    assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}
    # 3 semanas, train_weeks=1 -> 2 semanas de aplicación por estrategia
    assert all(len(p) == 2 for p in planes.values())
    # con train_weeks=1, wf-pico usa el pico de la semana anterior
    esperado = (weekly[0]["peak"]["buy_drop_pct"],
                weekly[0]["peak"]["sell_rise_pct"],
                weekly[0]["peak"]["interval_minutes"])
    assert planes["wf-pico"][0] == esperado
    # el oráculo usa el pico de la propia semana
    assert planes["oraculo"][1][0] == weekly[2]["peak"]["buy_drop_pct"]
    for r in resultados.values():
        assert "roi" in r and "total_equity" in r


def test_tournament_train_weeks_mayor_a_uno_usa_ventana_correcta():
    # 4 semanas, train_weeks=2 -> 2 semanas de aplicación (i=2, i=3).
    # Para wf-pico/wf-meseta con train_weeks>1, tournament() debe concatenar
    # EXACTAMENTE las train_weeks semanas previas (range(i - train_weeks, i))
    # y correr run_grid de nuevo sobre esa concatenación. Reconstruimos aquí,
    # de forma independiente, la ventana correcta para la primera semana de
    # aplicación (i=2 -> semanas [0, 1]) y comparamos el resultado exacto de
    # planes["wf-pico"][0]. Un off-by-one en el rango (p.ej.
    # range(i - train_weeks + 1, i), que concatenaría solo la semana 1) casi
    # seguro produce un pico distinto sobre datos sintéticos aleatorios, así
    # que este test lo detectaría.
    weekly = _weekly_sintetico(n_semanas=4)
    resultados, planes = tournament(weekly, 2, [1], 0.0, True, 10_000.0)

    assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}
    assert all(len(p) == len(weekly) - 2 for p in planes.values())

    ventana_correcta = pd.concat(
        [weekly[0]["wk"]["df"], weekly[1]["wk"]["df"]],
        ignore_index=True,
    )
    resultados_ventana = run_grid(ventana_correcta, [1], 0.0, True, 10_000.0)
    pico_esperado = select_peak(resultados_ventana)
    esperado = (pico_esperado["buy_drop_pct"],
                pico_esperado["sell_rise_pct"],
                pico_esperado["interval_minutes"])

    assert planes["wf-pico"][0] == esperado


def test_run_analysis_pipeline_completo():
    rng = np.random.default_rng(11)
    partes = []
    lunes = pd.date_range("2026-01-05", periods=4, freq="7D")
    for i in range(4):
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
        partes.append(make_df(prices, start=lunes[i].strftime("%Y-%m-%d 15:00")))
    df = pd.concat(partes, ignore_index=True)

    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)

    assert len(out["periods"]) == 4
    assert len(out["regret"]) == 3
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}
    assert 0.01 <= out["stats"]["median_drop"] <= 0.10
    assert isinstance(out["veredicto"], str) and len(out["veredicto"]) > 0

    # Con esta semilla (rng=11) el torneo real da:
    #   fija-mediana +5.72%  wf-pico +6.13%  wf-meseta +5.08%  oraculo +7.36%
    # es decir wf-pico le gana a fija-mediana -> rama "SE JUSTIFICA", nombrando
    # a WF-pico como la estrategia adaptativa ganadora. Verificamos el texto de
    # la rama Y que sea consistente con los ROI reales de out["torneo"]: si un
    # bug invirtiera el operador de comparación o mezclara qué ROI se compara
    # con cuál, esta aserción lo detectaría.
    assert "EL AUTO-AJUSTE SE JUSTIFICA" in out["veredicto"]
    assert "WF-pico" in out["veredicto"]
    assert out["torneo"]["wf-pico"]["roi"] > out["torneo"]["fija-mediana"]["roi"]
    assert out["torneo"]["wf-pico"]["roi"] >= out["torneo"]["wf-meseta"]["roi"]


def test_run_analysis_veredicto_no_se_justifica_cuando_fija_empata_adaptativas():
    # Para forzar la rama "NO SE JUSTIFICA" replicamos EXACTAMENTE la misma
    # semana (misma serie de precios) 3 veces. Con datos idénticos, el pico
    # óptimo de cada semana es idéntico, así que wf-pico/wf-meseta (que usan
    # los parámetros óptimos de la semana anterior) terminan aplicando los
    # mismos parámetros que fija-mediana (la mediana de picos idénticos es
    # ese mismo pico) sobre datos idénticos -> empatan en ROI. Como la
    # condición de "justifica" es un ">" estricto, un empate cae en la rama
    # "NO SE JUSTIFICA". Verificado ejecutando el pipeline (ver reporte).
    rng = np.random.default_rng(5)
    n_bars = 40
    n_semanas = 3
    base_prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, n_bars))
    lunes = pd.date_range("2026-01-05", periods=n_semanas, freq="7D")
    partes = [make_df(base_prices, start=lunes[i].strftime("%Y-%m-%d 15:00")) for i in range(n_semanas)]
    df = pd.concat(partes, ignore_index=True)

    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)

    assert len(out["periods"]) == 3
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}

    # Con esta semilla y semanas idénticas, el torneo real da un empate
    # exacto: fija-mediana == wf-pico == wf-meseta == oraculo (~-0.04% ROI)
    # -> rama "NO SE JUSTIFICA". Verificamos el texto de la rama Y que sea
    # consistente con los ROI reales de out["torneo"].
    assert "EL AUTO-AJUSTE NO SE JUSTIFICA" in out["veredicto"]
    assert out["torneo"]["fija-mediana"]["roi"] >= out["torneo"]["wf-pico"]["roi"]
    assert out["torneo"]["fija-mediana"]["roi"] >= out["torneo"]["wf-meseta"]["roi"]


@pytest.mark.parametrize("train_periods", ["0", "-3"])
def test_cli_train_periods_menor_a_uno_falla_limpio_sin_traceback(train_periods):
    # Mismo caso que antes (antes con --train-weeks), ahora con el flag
    # renombrado --train-periods. La validación vive en main() justo después
    # de parser.parse_args(), antes de load_dotenv()/load_bars(), así que
    # este subprocess nunca llega a tocar red ni caché.
    script = Path(__file__).resolve().parent / "walk_forward.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--train-periods", train_periods],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "--train-periods debe ser >= 1" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "ValueError" not in proc.stderr


@pytest.mark.parametrize("period", ["week", "month"])
def test_cli_period_acepta_week_y_month_sin_crashear(period):
    # No corre el pipeline completo (evitaría tocar red/caché real). Combina
    # un --period válido con --train-periods 0 (inválido, ya cubierto arriba)
    # para llegar hasta la validación de argparse de --period (que debe
    # aceptar "week"/"month" sin rechazarlos) y frenar ahí mismo con el error
    # ya conocido de --train-periods, sin tocar load_bars/red.
    script = Path(__file__).resolve().parent / "walk_forward.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--period", period, "--train-periods", "0"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "--train-periods debe ser >= 1" in proc.stderr
    assert "invalid choice" not in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_period_invalido_rechazado_por_argparse():
    script = Path(__file__).resolve().parent / "walk_forward.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--period", "quarter"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr


def test_run_analysis_con_period_month_pipeline_completo():
    rng = np.random.default_rng(13)
    partes = []
    meses_inicio = ["2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05"]
    for start in meses_inicio:
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
        partes.append(make_df(prices, start=f"{start} 15:00"))
    df = pd.concat(partes, ignore_index=True)

    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True,
                        buy_amount=10_000.0, period="month")

    assert len(out["periods"]) == 4
    assert [p["wk"]["label"] for p in out["periods"]] == ["2026-M01", "2026-M02", "2026-M03", "2026-M04"]
    assert len(out["regret"]) == 3
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}
    assert isinstance(out["veredicto"], str) and len(out["veredicto"]) > 0
