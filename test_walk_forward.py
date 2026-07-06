import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from optimize import simulate, new_state, MAX_BUYS, buy_hold_roi, can_buy
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

    assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}
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

    assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}
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
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}
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
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}

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
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}
    assert isinstance(out["veredicto"], str) and len(out["veredicto"]) > 0


# ---------------------------------------------------------------------------
# Callback on_trade (spec fase 2.7)
# ---------------------------------------------------------------------------

def test_on_trade_emite_eventos_en_orden():
    # Con ZIGZAG la secuencia de operaciones es conocida (ver comentario
    # arriba de ZIGZAG): init@100, grid@94, sell@99, sell@106, init@100.
    df = make_df(ZIGZAG)
    eventos = []
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, on_trade=eventos.append)

    assert [e["type"] for e in eventos] == ["BUY_INIT", "BUY_GRID", "SELL", "SELL", "BUY_INIT"]
    assert [e["price"] for e in eventos] == [100.0, 94.0, 99.0, 106.0, 100.0]

    venta = eventos[2]
    assert venta["buy_price"] == pytest.approx(94.0)
    assert venta["profit"] == pytest.approx(531.9148936, abs=1e-4)
    assert venta["timestamp"] == df["timestamp"].iloc[2]
    assert venta["open_positions"] == 1          # quedó solo el lote de 100

    assert eventos[-1]["open_positions"] == 1
    assert eventos[-1]["pool"] == pytest.approx(1131.9148936 * 0.9, abs=1e-4)  # pool tras restar bonus pool/10

    # El callback no altera el resultado numérico
    r2 = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert r["roi"] == pytest.approx(r2["roi"])


# ---------------------------------------------------------------------------
# Drawdown máximo (spec 2026-07-06-risk-mechanisms, fase 1.2)
# ---------------------------------------------------------------------------

def test_simulate_max_drawdown_basico():
    # drop=5%, rise=5%, buy=10000, pool ON, secuencia [100, 80, 90]:
    #   bar0: compra inicial @100 (qty 100, cash 90000) -> equity 100000
    #   bar1: 80 <= 95, compra grid @80 (qty 125, cash 80000)
    #         -> equity 80000 + 225*80 = 98000 -> dd = 2%
    #   bar2: 90 >= 84 (target venta s/80), vende qty 125 (revenue 11250,
    #         cash 91250) -> equity 91250 + 100*90 = 100250 -> pico nuevo
    df = make_df([100, 80, 90])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert r["max_drawdown_pct"] == pytest.approx(2.0)
    assert r["state"]["equity_peak"] == pytest.approx(100_250.0)
    assert r["state"]["max_dd"] == pytest.approx(0.02)


def test_simulate_max_drawdown_encadenado_persiste_pico():
    # [100, 80, 70]: bar2 compra @70 (target 76 s/80), equity final
    # 70000 + 367.857*70 = 95750 -> dd 4.25% respecto del pico 100000.
    # Si el pico no persistiera en state, la corrida encadenada mediría el
    # dd del segundo tramo contra su propio primer equity (95750) y daría ~0.
    df = make_df([100, 80, 70])
    completo = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert completo["max_drawdown_pct"] == pytest.approx(4.25)

    df_a = df.iloc[:2].reset_index(drop=True)
    df_b = df.iloc[2:].reset_index(drop=True)
    r1 = simulate(df_a, MAX_BUYS, 0.05, 0.05, 0.0)
    r2 = simulate(df_b, MAX_BUYS, 0.05, 0.05, 0.0, state=r1["state"])
    assert r2["max_drawdown_pct"] == pytest.approx(completo["max_drawdown_pct"])


def test_simulate_tolera_state_viejo_sin_claves_nuevas():
    # Un state serializado antes de este cambio no tiene equity_peak/max_dd:
    # simulate debe tolerarlo con defaults en vez de romper con KeyError.
    st = new_state()
    del st["equity_peak"]
    del st["max_dd"]
    r = simulate(make_df(ZIGZAG), MAX_BUYS, 0.05, 0.05, 0.0, state=st)
    assert r["roi"] == pytest.approx(1.1319148936, abs=1e-6)
    assert "max_drawdown_pct" in r


# ---------------------------------------------------------------------------
# Buy & hold de referencia (spec fase 1.1)
# ---------------------------------------------------------------------------

def test_buy_hold_roi_serie_que_sube():
    df = make_df([100, 110, 120])
    r = buy_hold_roi(df)
    assert r["roi"] == pytest.approx(20.0)
    assert r["profit"] == pytest.approx(20_000.0)
    assert r["total_equity"] == pytest.approx(120_000.0)
    assert r["max_drawdown_pct"] == pytest.approx(0.0)


def test_buy_hold_roi_con_drawdown():
    # 100 -> 80 (dd 20%) -> 110 (cierra +10%)
    df = make_df([100, 80, 110])
    r = buy_hold_roi(df)
    assert r["roi"] == pytest.approx(10.0)
    assert r["max_drawdown_pct"] == pytest.approx(20.0)


def test_buy_hold_roi_respeta_starting_cash():
    df = make_df([100, 150])
    r = buy_hold_roi(df, starting_cash=10_000.0)
    assert r["total_equity"] == pytest.approx(15_000.0)
    assert r["roi"] == pytest.approx(50.0)


def test_tournament_incluye_buy_hold_sobre_periodos_de_aplicacion():
    weekly = _weekly_sintetico(n_semanas=3)
    resultados, planes = tournament(weekly, 1, [1], 0.0, True, 10_000.0)

    assert "buy-hold" in resultados
    # B&H se calcula SOLO sobre los períodos de aplicación (train_periods=1
    # -> semanas 1 y 2), no sobre el rango completo.
    app_df = pd.concat([weekly[1]["wk"]["df"], weekly[2]["wk"]["df"]], ignore_index=True)
    first = float(app_df["close"].iloc[0])
    last  = float(app_df["close"].iloc[-1])
    assert resultados["buy-hold"]["roi"] == pytest.approx((last - first) / first * 100)
    # buy-hold no participa de la tabla de parámetros por período
    assert set(planes) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}


def test_veredicto_siempre_menciona_buy_hold():
    rng = np.random.default_rng(11)
    partes = []
    lunes = pd.date_range("2026-01-05", periods=4, freq="7D")
    for i in range(4):
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
        partes.append(make_df(prices, start=lunes[i].strftime("%Y-%m-%d 15:00")))
    df = pd.concat(partes, ignore_index=True)

    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)
    assert "buy & hold" in out["veredicto"].lower()
    # Consistencia dirección/texto: si el mejor ROI realista supera al B&H
    # el texto dice que el grid le gana; si no, advierte con "OJO".
    bh = out["torneo"]["buy-hold"]["roi"]
    mejor_real = max(out["torneo"]["fija-mediana"]["roi"],
                     out["torneo"]["wf-pico"]["roi"],
                     out["torneo"]["wf-meseta"]["roi"])
    if mejor_real > bh:
        assert "le gana" in out["veredicto"]
    else:
        assert "OJO" in out["veredicto"]


# ---------------------------------------------------------------------------
# Mecanismo anti-crash: cooldown temporal (spec fase 2.2)
# ---------------------------------------------------------------------------

def test_can_buy_cooldown_y_pila():
    compras = [{"price": 100.0, "qty": 1.0}]
    assert can_buy(compras, 10, 90.0)
    assert not can_buy(compras, 10, 90.0, cooldown_remaining_min=1.0)
    assert can_buy([], 10, 90.0, cooldown_remaining_min=5.0)   # compra inicial ignora cooldown
    assert not can_buy(compras * 10, 10, 90.0)                 # pila llena


def test_cooldown_bloquea_compras_hasta_vencer():
    # interval=1, cooldown=2 min, drop 5% rise 5%, [100, 94, 88, 83, 78]:
    #   sin cooldown compra en cada vela (5 compras).
    #   con cooldown: init@100, grid@94 (arranca cooldown=2),
    #   88 bloqueada (resta 1), 83 compra (resta 0), 78 bloqueada (resta 1).
    df = make_df([100, 94, 88, 83, 78])
    sin = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    con = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2)
    assert sin["buys"] == 5
    assert con["buys"] == 3
    assert [p["price"] for p in con["state"]["purchases"]] == [100.0, 94.0, 83.0]


def test_cooldown_persiste_entre_llamadas_encadenadas():
    df = make_df([100, 94, 88, 83, 78])
    a = df.iloc[:3].reset_index(drop=True)
    b = df.iloc[3:].reset_index(drop=True)
    completo = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2)
    r1 = simulate(a, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2)
    r2 = simulate(b, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2, state=r1["state"])
    assert r2["buys"] == completo["buys"] == 3
    assert r2["total_equity"] == pytest.approx(completo["total_equity"])


def test_cooldown_no_afecta_compra_inicial_ni_ventas():
    # init@100, grid@94 (cooldown=10 arranca), 99>=98.7 vende lote de 94,
    # 105.1>=105 vende lote de 100 (pila vacía), re-pivot @100 pese al
    # cooldown vigente. Ventas nunca bloqueadas, re-pivot tampoco.
    df = make_df([100, 94, 99, 105.1, 100])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=10)
    assert r["buys"] == 3
    assert r["sells"] == 2
    assert r["open_positions"] == 1


# ---------------------------------------------------------------------------
# Mecanismo anti-crash: slots reservados por profundidad (spec fase 2.3)
# ---------------------------------------------------------------------------

def test_can_buy_slots_reservados():
    # pivot=100, max_buys=3, reserved=1: el 3er slot exige caída >= 20%
    compras = [{"price": 100.0, "qty": 1.0}, {"price": 95.0, "qty": 1.0}]
    assert not can_buy(compras, 3, 85.0, reserved_slots=1, deep_drop_pct=0.20)  # 85 > 80
    assert can_buy(compras, 3, 79.0, reserved_slots=1, deep_drop_pct=0.20)      # 79 <= 80
    # con un solo lote (slot no reservado) compra normal
    assert can_buy(compras[:1], 3, 94.0, reserved_slots=1, deep_drop_pct=0.20)


def test_slots_reservados_frenan_sin_caida_profunda():
    # max_buys=3, reserved=1, deep=20% (umbral: pivot 100 -> 80),
    # drop 5%, rise 99% (sin ventas), [100, 95, 90, 85, 79]:
    #   sin mecanismo: compra 100, 95, 90 (pila llena en 3).
    #   con mecanismo: compra 100, 95; 90 y 85 bloqueadas (> 80); 79 compra.
    df = make_df([100, 95, 90, 85, 79])
    sin = simulate(df, 3, 0.05, 0.99, 0.0)
    con = simulate(df, 3, 0.05, 0.99, 0.0, reserved_slots=1, deep_drop_pct=0.20)
    assert [p["price"] for p in sin["state"]["purchases"]] == [100.0, 95.0, 90.0]
    assert [p["price"] for p in con["state"]["purchases"]] == [100.0, 95.0, 79.0]


def test_slots_reservados_pivot_se_renueva_tras_vaciar_pila():
    # [100, 95, 105, 105.5, 100, 95, 90, 79], drop 5% rise 5%,
    # max_buys=3, reserved=1, deep=20%:
    #   init@100, grid@95, 105>=99.75 vende lote 95, 105.5>=105 vende lote
    #   100 (pila vacía), re-pivot init@100, grid@95, 90 bloqueada
    #   (nuevo pivot 100 -> umbral 80), 79 compra (slot reservado).
    df = make_df([100, 95, 105, 105.5, 100, 95, 90, 79])
    r = simulate(df, 3, 0.05, 0.05, 0.0, reserved_slots=1, deep_drop_pct=0.20)
    assert r["buys"] == 5
    assert r["sells"] == 2
    assert [p["price"] for p in r["state"]["purchases"]] == [100.0, 95.0, 79.0]
