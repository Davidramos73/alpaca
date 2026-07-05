# Granularidad de período configurable (semana/mes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un flag `--period {week,month}` a `walk_forward.py` para poder correr el análisis de estabilidad y el torneo con períodos mensuales además de semanales, reutilizando el pipeline existente sin duplicarlo.

**Architecture:** `split_weeks()` se generaliza a `split_periods(df, period)` (soporta `"week"`/`"month"`). El resto del pipeline (`run_grid`, `select_peak/plateau`, `lag1_corr`, `median_params`, `regret_series`, `simulate_adaptive`, `tournament`, `run_analysis`) ya es agnóstico al tamaño del período — solo se renombran parámetros (`weekly`→`periods`, `train_weeks`→`train_periods`) y se agrega un parámetro `period` a `run_analysis()` que se threadea desde el nuevo flag CLI. El reporte usa la palabra genérica "período" en vez de alternar "semana"/"mes" (ver Task 3 para el porqué).

**Tech Stack:** Python 3, pandas, numpy, pytest (igual que el resto del proyecto).

**Spec:** `docs/superpowers/specs/2026-07-05-monthly-period-design.md`

## Global Constraints

- Todos los archivos siguen en la raíz de `alpaca/` (layout plano): se modifican `walk_forward.py` y `test_walk_forward.py` únicamente.
- Los tests usan SOLO datos sintéticos — nunca llaman a Alpaca ni requieren `.env` (regla ya establecida en el proyecto).
- Sin cambios en `optimize.py`, `simulate()` ni `new_state()`.
- El texto de `build_report()` y de los mensajes internos de `run_analysis()` usa la palabra genérica **"período"** (masculino, sin necesidad de alternar género/concordancia entre "semana"/"mes") en vez de period-word branching — ver la corrección de diseño en la spec, sección 4. La granularidad elegida se comunica vía un campo explícito `granularidad: {period}` en la cabecera del log y vía las etiquetas de cada fila (`2026-W03` vs `2026-M03`).
- Es una ruptura de compatibilidad intencional: `--train-weeks` deja de existir (pasa a `--train-periods`), y las columnas CSV `week_label/week_start/week_end` pasan a `period_label/period_start/period_end`. Los CSVs/logs ya generados (`walkforward_TSLA_*.csv`, `walkforward_SPCX_*.csv`) NO se migran ni regeneran.
- Comandos se corren desde `/home/david/Repos/David/Inversiones/invertirCarlos/alpaca`.
- Mensajes de commit terminan con `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

### Task 1: `split_periods()` — generaliza `split_weeks()` a semana/mes

**Files:**
- Modify: `walk_forward.py:21-32` (reemplaza `split_weeks`), y `walk_forward.py:140` (único caller, dentro de `run_analysis`)
- Modify: `test_walk_forward.py:10-21` (import), `test_walk_forward.py:77-93` (test existente)

**Interfaces:**
- Produces: `split_periods(df_1min: pd.DataFrame, period: str) -> list[dict]` — `period` es `"week"` (idéntico comportamiento a `split_weeks` actual, label `"{year}-W{week:02d}"`) o `"month"` (agrupa por año-mes calendario, label `"{year}-M{month:02d}"`). Mismo shape de retorno: lista de dicts con `label`/`start`/`end`/`df` (índice reseteado), orden cronológico, omite períodos sin velas, incluye parciales tal cual.
- `run_analysis()` (sin cambios de firma en esta task — eso es Task 2) sigue funcionando exactamente igual, ahora llamando `split_periods(df_1min, "week")` en vez de `split_weeks(df_1min)`.

- [ ] **Step 1: Escribir/adaptar los tests que fallan**

En `test_walk_forward.py`, cambiar el import (línea ~10-21):

```python
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
```

Reemplazar el test existente `test_split_weeks_semanas_iso_y_huecos` (líneas 77-93) por:

```python
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
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: FAIL con `ImportError: cannot import name 'split_periods'`.

- [ ] **Step 3: Implementar `split_periods()` en `walk_forward.py`**

Reemplazar la función `split_weeks` completa (líneas 21-32) por:

```python
def split_periods(df_1min: pd.DataFrame, period: str) -> list[dict]:
    ts = df_1min["timestamp"]
    if period == "week":
        iso = ts.dt.isocalendar()
        year_key, unit_key = iso["year"], iso["week"]
        label_prefix = "W"
    elif period == "month":
        year_key, unit_key = ts.dt.year, ts.dt.month
        label_prefix = "M"
    else:
        raise ValueError(f"period desconocido: {period!r} (usar 'week' o 'month')")

    periods = []
    for (year, unit), g in df_1min.groupby([year_key, unit_key], sort=True):
        g = g.reset_index(drop=True)
        periods.append({
            "label": f"{year}-{label_prefix}{unit:02d}",
            "start": g["timestamp"].iloc[0],
            "end":   g["timestamp"].iloc[-1],
            "df":    g,
        })
    return periods
```

Y en `run_analysis()` (línea 140), cambiar únicamente:

```python
    weeks = split_weeks(df_1min)
```

por:

```python
    weeks = split_periods(df_1min, "week")
```

(El resto de `run_analysis()` no cambia en esta task — sigue usando `weeks`, `train_weeks`, `weekly` como hoy. Ese renombrado es Task 2.)

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 20 PASS (19 preexistentes, con `test_split_weeks_semanas_iso_y_huecos` reemplazado 1-a-1 por `test_split_periods_semanas_iso_y_huecos`, más 1 test nuevo `test_split_periods_meses_calendario_y_hueco`).

- [ ] **Step 5: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: generalizar split_weeks() a split_periods() con soporte semana/mes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Generalizar nombres (`weekly`→`periods`, `train_weeks`→`train_periods`) y threadear `period` por `run_analysis()`

**Files:**
- Modify: `walk_forward.py` (funciones `regret_series`, `tournament`, `simulate_adaptive`, `run_analysis`; un ajuste mínimo de 1 línea cada uno en `build_report()` y en `main()` para no romper el pipeline)
- Test: `test_walk_forward.py` (2 llamadas con keyword `train_weeks=` y 1 acceso a `out["weekly"]`)

**Interfaces:**
- Consumes: `split_periods` (Task 1).
- Produces: `regret_series(periods: list[dict], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]` (mismo comportamiento, parámetro renombrado).
- Produces: `tournament(periods: list[dict], train_periods: int, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> tuple[dict, dict]` (mismo comportamiento, parámetros renombrados).
- Produces: `simulate_adaptive(period_dfs: list[pd.DataFrame], params_per_period: list[tuple[float, float, int]], fee_pct: float, use_pool: bool, buy_amount: float) -> dict` (mismo comportamiento, parámetros renombrados).
- Produces: `run_analysis(df_1min: pd.DataFrame, intervals: list[int], train_periods: int, fee_pct: float, use_pool: bool, buy_amount: float, period: str = "week") -> dict` — nuevo parámetro `period` (default `"week"`, retrocompatible con las llamadas existentes que no lo pasan); llama a `split_periods(df_1min, period)`; el dict de retorno cambia la clave `"weekly"` por `"periods"` (mismas demás claves: `stats`, `regret`, `torneo`, `planes`, `veredicto`).
- Nota: esta task NO toca `build_report()` más allá de una línea de compatibilidad, ni agrega el flag CLI — eso es Task 3. Sin este ajuste mínimo, `main()` quedaría roto (haría `KeyError: 'weekly'`) hasta que Task 3 termine; por eso se incluye acá aunque el resto de `build_report()`/`main()` no cambie todavía.

- [ ] **Step 1: Escribir los tests que fallan**

En `test_walk_forward.py`, en `test_run_analysis_pipeline_completo` (buscar `train_weeks=1` — es la única ocurrencia junto con la de abajo), cambiar:

```python
    out = run_analysis(df, intervals=[1], train_weeks=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)

    assert len(out["weekly"]) == 4
```

por:

```python
    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)

    assert len(out["periods"]) == 4
```

En `test_run_analysis_veredicto_no_se_justifica_cuando_fija_empata_adaptativas`, cambiar:

```python
    out = run_analysis(df, intervals=[1], train_weeks=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)
```

por:

```python
    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)
```

Agregar además un test nuevo (al final del archivo) que fija el comportamiento de `run_analysis()` con `period="month"` — el pipeline completo debe correr sobre períodos mensuales sin asumir semanas en ningún lado:

```python
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
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: FAIL. Los dos tests con `train_periods=1` fallan con `TypeError: run_analysis() got an unexpected keyword argument 'train_periods'`. El test nuevo `test_run_analysis_con_period_month_pipeline_completo` falla con `TypeError: run_analysis() got an unexpected keyword argument 'period'` (todavía no existe ese parámetro).

- [ ] **Step 3: Implementar los renombres en `walk_forward.py`**

Reemplazar `regret_series` completa por:

```python
def regret_series(periods: list[dict], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]:
    rows = []
    for i in range(1, len(periods)):
        prev = periods[i - 1]["peak"]
        d, r, m = prev["buy_drop_pct"], prev["sell_rise_pct"], prev["interval_minutes"]
        sub = periods[i]["wk"]["df"].iloc[::m].reset_index(drop=True)
        applied = simulate(sub, MAX_BUYS, d, r, fee_pct, use_pool, buy_amount, m)
        own = periods[i]["peak"]["roi"]
        rows.append({
            "label":       periods[i]["wk"]["label"],
            "own_roi":     own,
            "applied_roi": applied["roi"],
            "regret":      own - applied["roi"],
        })
    return rows
```

Reemplazar `simulate_adaptive` completa por:

```python
def simulate_adaptive(period_dfs: list[pd.DataFrame], params_per_period: list[tuple[float, float, int]], fee_pct: float, use_pool: bool, buy_amount: float) -> dict:
    state = None
    result = None
    for df_period, (drop, rise, interval) in zip(period_dfs, params_per_period, strict=True):
        sub = df_period.iloc[::interval].reset_index(drop=True)
        result = simulate(sub, MAX_BUYS, drop, rise, fee_pct, use_pool, buy_amount, interval, state=state)
        state = result["state"]
    return result
```

Reemplazar `tournament` completa por:

```python
def tournament(periods: list[dict], train_periods: int, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> tuple[dict, dict]:
    def params_of(r: dict) -> tuple[float, float, int]:
        return (r["buy_drop_pct"], r["sell_rise_pct"], r["interval_minutes"])

    app = range(train_periods, len(periods))
    app_dfs = [periods[i]["wk"]["df"] for i in app]

    planes = {"fija-mediana": [], "wf-pico": [], "wf-meseta": [], "oraculo": []}
    for i in app:
        planes["fija-mediana"].append(median_params([periods[j]["peak"] for j in range(i)]))

        if train_periods == 1:
            train_results = periods[i - 1]["results"]
        else:
            train_df = pd.concat(
                [periods[j]["wk"]["df"] for j in range(i - train_periods, i)],
                ignore_index=True,
            )
            train_results = run_grid(train_df, intervals, fee_pct, use_pool, buy_amount)

        planes["wf-pico"].append(params_of(select_peak(train_results)))
        planes["wf-meseta"].append(params_of(select_plateau(train_results)[0]))
        planes["oraculo"].append(params_of(periods[i]["peak"]))

    resultados = {
        nombre: simulate_adaptive(app_dfs, plan, fee_pct, use_pool, buy_amount)
        for nombre, plan in planes.items()
    }
    return resultados, planes
```

Reemplazar `run_analysis` completa por:

```python
def run_analysis(df_1min: pd.DataFrame, intervals: list[int], train_periods: int, fee_pct: float, use_pool: bool, buy_amount: float, period: str = "week") -> dict:
    periods_list = split_periods(df_1min, period)
    if len(periods_list) <= train_periods + 1:
        raise SystemExit(f"Error: {len(periods_list)} período(s) de datos; se necesitan al menos {train_periods + 2}.")

    periods = []
    for n, p in enumerate(periods_list, 1):
        print(f"  Grid período {n}/{len(periods_list)} ({p['label']})", end="\r")
        results = run_grid(p["df"], intervals, fee_pct, use_pool, buy_amount)
        plateau, plateau_score = select_plateau(results)
        periods.append({
            "wk": p,
            "results": results,
            "peak": select_peak(results),
            "plateau": plateau,
            "plateau_score": plateau_score,
        })
    print()

    drops = [p["peak"]["buy_drop_pct"] for p in periods]
    rises = [p["peak"]["sell_rise_pct"] for p in periods]
    ints  = [p["peak"]["interval_minutes"] for p in periods]
    q75d, q25d = np.percentile(drops, [75, 25])
    q75r, q25r = np.percentile(rises, [75, 25])
    stats = {
        "median_drop": float(np.median(drops)),
        "median_rise": float(np.median(rises)),
        "std_drop":    float(np.std(drops)),
        "std_rise":    float(np.std(rises)),
        "iqr_drop":    float(q75d - q25d),
        "iqr_rise":    float(q75r - q25r),
        "corr_drop":   lag1_corr(drops),
        "corr_rise":   lag1_corr(rises),
        "interval_counts": dict(pd.Series(ints).value_counts().sort_index()),
    }

    regret = regret_series(periods, fee_pct, use_pool, buy_amount)
    torneo, planes = tournament(periods, train_periods, intervals, fee_pct, use_pool, buy_amount)

    fija = torneo["fija-mediana"]["roi"]
    adaptativas = {"WF-pico": torneo["wf-pico"]["roi"], "WF-meseta": torneo["wf-meseta"]["roi"]}
    mejor_adapt = max(adaptativas, key=adaptativas.get)
    if adaptativas[mejor_adapt] > fija:
        veredicto = (
            f"EL AUTO-AJUSTE SE JUSTIFICA: {mejor_adapt} ({adaptativas[mejor_adapt]:+.2f}%) supera a "
            f"Fija-mediana ({fija:+.2f}%). Techo teórico (Oráculo): {torneo['oraculo']['roi']:+.2f}%."
        )
    else:
        d, r, m = median_params([p["peak"] for p in periods])
        veredicto = (
            f"EL AUTO-AJUSTE NO SE JUSTIFICA: Fija-mediana ({fija:+.2f}%) le gana a "
            f"WF-pico ({adaptativas['WF-pico']:+.2f}%) y WF-meseta ({adaptativas['WF-meseta']:+.2f}%). "
            f"Recomendación: parámetros fijos drop={d*100:.0f}% rise={r*100:.0f}% intervalo={m} min."
        )

    return {"periods": periods, "stats": stats, "regret": regret,
            "torneo": torneo, "planes": planes, "veredicto": veredicto}
```

Ajuste mínimo en `build_report()` (busca la línea que empieza con `weekly, stats, regret = out[`): cambiar

```python
    weekly, stats, regret = out["weekly"], out["stats"], out["regret"]
```

por:

```python
    weekly, stats, regret = out["periods"], out["stats"], out["regret"]
```

(Solo el lado derecho cambia. El resto de `build_report()` sigue usando la variable local `weekly` sin tocar — se renombra completo recién en Task 3.)

Ajuste mínimo en `main()` (busca `for w in out[`): cambiar

```python
    } for w in out["weekly"]]
```

por:

```python
    } for w in out["periods"]]
```

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 21 PASS (20 previos + 1 nuevo, `test_run_analysis_con_period_month_pipeline_completo`).

Sanity extra (usa el caché real existente, sin red):
Run: `python3 walk_forward.py --symbol TSLA --date-start 2026-01-01 --date-end 2026-01-31 --intervals 20 2>&1 | tail -5`
Expected: corre sin error y termina imprimiendo rutas de log/CSV (usando el default `period="week"` de siempre).

- [ ] **Step 5: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "refactor: generalizar weekly/train_weeks a periods/train_periods y threadear period por run_analysis()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: CLI (`--period`, `--train-periods`), reporte genérico y CSV

**Files:**
- Modify: `walk_forward.py` (`build_report`, `main`)
- Test: `test_walk_forward.py` (tests de CLI actualizados/nuevos — el test end-to-end de `period="month"` para `run_analysis()` ya se agregó en la Task 2)

**Interfaces:**
- Consumes: `run_analysis(..., period: str = "week")` (Task 2), `split_periods` (Task 1).
- Produces: `build_report(symbol: str, out: dict, train_periods: int, intervals: list[int], buy_amount: float, fee_pct: float, use_pool: bool, period: str) -> list[str]` — texto genérico ("período" en vez de alternar semana/mes), con `granularidad: {period}` explícito en la cabecera.
- Produces: CLI con `--period {week,month}` (default `week`) y `--train-periods` (reemplaza a `--train-weeks`, mismo default `1`, misma validación).
- CSV: columnas `period_label, period_start, period_end, bars, best_drop, best_rise, best_interval, best_roi, plateau_drop, plateau_rise, plateau_interval, plateau_roi`.

- [ ] **Step 1: Escribir los tests que fallan**

Reemplazar el test de CLI existente `test_cli_train_weeks_menor_a_uno_falla_limpio_sin_traceback` (buscar `@pytest.mark.parametrize("train_weeks"`, al final del archivo) por:

```python
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
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: FAIL en los 4 tests de CLI nuevos/modificados: `test_cli_train_periods_menor_a_uno_falla_limpio_sin_traceback` (2 casos) falla porque `--train-periods` todavía no existe en `main()` (argparse lo rechaza con "unrecognized arguments" en vez de disparar la validación esperada), y `test_cli_period_acepta_week_y_month_sin_crashear` (2 casos) / `test_cli_period_invalido_rechazado_por_argparse` fallan porque `--period` tampoco existe todavía.

- [ ] **Step 3: Implementar CLI y reporte en `walk_forward.py`**

Reemplazar `build_report` completa por:

```python
def build_report(symbol: str, out: dict, train_periods: int, intervals: list[int], buy_amount: float, fee_pct: float, use_pool: bool, period: str) -> list[str]:
    periods, stats, regret = out["periods"], out["stats"], out["regret"]
    torneo, planes = out["torneo"], out["planes"]

    lines = [
        SEP,
        f"  WALK-FORWARD {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        SEP,
        f"  Períodos: {len(periods)}  ({periods[0]['wk']['label']} → {periods[-1]['wk']['label']})"
        f"   |   granularidad: {period}   |   train_periods: {train_periods}   |   intervalos: {intervals}",
        f"  Monto por compra: ${buy_amount:,.0f}   |   fee: {fee_pct*100:.3f}%   |   pool: {'ON' if use_pool else 'OFF'}",
        SEP,
        "",
        "  1) ESTABILIDAD DE LOS ÓPTIMOS POR PERÍODO",
        SEP2,
        f"  {'período':<10}  {'velas':>6}  {'drop':>5}  {'rise':>5}  {'min':>4}  {'ROI%':>8}   |  {'meseta d/r/min':>15}  {'ROI%':>8}",
        SEP2,
    ]
    for p in periods:
        pk, q = p["peak"], p["plateau"]
        lines.append(
            f"  {p['wk']['label']:<10}  {len(p['wk']['df']):>6}  "
            f"{pk['buy_drop_pct']*100:>4.0f}%  {pk['sell_rise_pct']*100:>4.0f}%  {pk['interval_minutes']:>4}  {pk['roi']:>+8.2f}   |  "
            f"{q['buy_drop_pct']*100:>4.0f}/{q['sell_rise_pct']*100:>3.0f}/{q['interval_minutes']:>4}  {q['roi']:>+8.2f}"
        )
    lines += [
        SEP2,
        f"  drop óptimo : mediana {stats['median_drop']*100:.1f}%  desvío {stats['std_drop']*100:.2f}pp  IQR {stats['iqr_drop']*100:.1f}pp  autocorr lag-1 {stats['corr_drop']:+.2f}",
        f"  rise óptimo : mediana {stats['median_rise']*100:.1f}%  desvío {stats['std_rise']*100:.2f}pp  IQR {stats['iqr_rise']*100:.1f}pp  autocorr lag-1 {stats['corr_rise']:+.2f}",
        f"  intervalos ganadores: {stats['interval_counts']}",
        f"  (n = {len(periods)} períodos: muestra chica, interpretar la autocorrelación con cautela)",
        "",
        "  REGRET (usar el óptimo del período anterior vs el propio, períodos aislados)",
        SEP2,
        f"  {'período':<10}  {'ROI propio':>10}  {'ROI aplicado':>12}  {'regret':>8}",
        SEP2,
    ]
    for row in regret:
        lines.append(f"  {row['label']:<10}  {row['own_roi']:>+10.2f}  {row['applied_roi']:>+12.2f}  {row['regret']:>8.2f}")
    regrets = [row["regret"] for row in regret]
    lines += [
        SEP2,
        f"  regret promedio {np.mean(regrets):.2f}pp  |  mediana {np.median(regrets):.2f}pp  |  peor {np.max(regrets):.2f}pp",
        "",
        "  2) TORNEO DE ESTRATEGIAS (portfolio continuo, períodos de aplicación: "
        f"{len(periods) - train_periods})",
        SEP2,
        f"  {'estrategia':<14}  {'ROI%':>8}  {'Ganancia':>12}  {'Capital':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Fees':>10}",
        SEP2,
    ]
    for nombre in ("fija-mediana", "wf-pico", "wf-meseta", "oraculo"):
        r = torneo[nombre]
        lines.append(
            f"  {nombre:<14}  {r['roi']:>+8.2f}  ${r['profit']:>+11,.0f}  ${r['total_equity']:>11,.0f}  "
            f"{r['buys']:>7}  {r['sells']:>6}  {r['open_positions']:>5}  ${r['total_fees']:>9,.0f}"
        )
    lines += [
        SEP2,
        "",
        "  PARÁMETROS USADOS POR PERÍODO (drop%/rise%/min)",
        SEP2,
        "  " + f"{'período':<10}" + "".join(f"  {n:>14}" for n in planes),
        SEP2,
    ]
    app_periods = periods[train_periods:]
    for k, p in enumerate(app_periods):
        celdas = "".join(
            f"  {planes[n][k][0]*100:>4.0f}/{planes[n][k][1]*100:>3.0f}/{planes[n][k][2]:>4}"
            for n in planes
        )
        lines.append(f"  {p['wk']['label']:<10}{celdas}")
    lines += [SEP2, "", "  3) VEREDICTO", SEP2, f"  {out['veredicto']}", SEP]
    return lines
```

Reemplazar `main` completa por:

```python
def main():
    parser = argparse.ArgumentParser(description="Walk-forward: estabilidad de óptimos por período y torneo de estrategias")
    parser.add_argument("--symbol",      type=str,   default="TSLA")
    parser.add_argument("--date-start",  type=str,   default="2026-01-01")
    parser.add_argument("--date-end",    type=str,   default="2026-06-28")
    parser.add_argument("--buy-amount",  type=float, default=10_000.0)
    parser.add_argument("--fee-pct",     type=float, default=0.0)
    parser.add_argument("--no-profit-pool", action="store_true")
    parser.add_argument("--intervals",   type=str,   default="20", help="Intervalos de revisión en minutos, separados por coma")
    parser.add_argument("--period",      type=str,   default="week", choices=["week", "month"], help="Granularidad de los períodos (default: week)")
    parser.add_argument("--train-periods", type=int, default=1, help="Períodos previos usados para optimizar (default: 1)")
    args = parser.parse_args()

    if args.train_periods < 1:
        parser.error("--train-periods debe ser >= 1")

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    intervals  = sorted({max(1, int(v.strip())) for v in args.intervals.split(",") if v.strip()})
    use_pool   = not args.no_profit_pool

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    out = run_analysis(df_1min, intervals, args.train_periods, args.fee_pct, use_pool, args.buy_amount, period=args.period)

    lines = build_report(symbol, out, args.train_periods, intervals, args.buy_amount, args.fee_pct, use_pool, args.period)
    print("\n".join(lines))

    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"walkforward_{symbol}_{run_ts}.log"
    csv_path = f"walkforward_{symbol}_{run_ts}.csv"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    filas = [{
        "period_label":     p["wk"]["label"],
        "period_start":     p["wk"]["start"],
        "period_end":       p["wk"]["end"],
        "bars":             len(p["wk"]["df"]),
        "best_drop":        p["peak"]["buy_drop_pct"],
        "best_rise":        p["peak"]["sell_rise_pct"],
        "best_interval":    p["peak"]["interval_minutes"],
        "best_roi":         p["peak"]["roi"],
        "plateau_drop":     p["plateau"]["buy_drop_pct"],
        "plateau_rise":     p["plateau"]["sell_rise_pct"],
        "plateau_interval": p["plateau"]["interval_minutes"],
        "plateau_roi":      p["plateau"]["roi"],
    } for p in out["periods"]]
    pd.DataFrame(filas).to_csv(csv_path, index=False)

    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verificar que pasan todos**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 24 PASS. Desglose: 21 previos (donde el viejo `test_cli_train_weeks_menor_a_uno_falla_limpio_sin_traceback`, parametrizado en 2 casos, se reemplaza 1-a-1 por `test_cli_train_periods_menor_a_uno_falla_limpio_sin_traceback` con los mismos 2 casos — sin cambio neto de cantidad) + 3 tests nuevos: `test_cli_period_acepta_week_y_month_sin_crashear` parametrizado en `["week", "month"]` (2) + `test_cli_period_invalido_rechazado_por_argparse` (1).

- [ ] **Step 5: Corrida real con `--period month` sobre el caché existente (criterio de éxito del spec)**

Run: `python3 walk_forward.py --symbol TSLA --date-start 2025-07-01 --date-end 2026-07-01 --intervals 20 --period month --train-periods 1`

Expected: usa el caché `cache_TSLA_20250701_20260701_1Min.pkl` (sin red, ya descargado en una sesión anterior), corre ~12 períodos mensuales, imprime las 3 secciones con etiquetas `2025-M07`...`2026-M07` y la línea de cabecera mostrando `granularidad: month`, y guarda `walkforward_TSLA_<timestamp>.log`/`.csv` con columnas `period_label,period_start,...`. Comparar el veredicto mensual contra el semanal ya registrado en `docs/walk-forward-log.md` (fuera del alcance de este plan agregarlo a la bitácora — lo hace el usuario o una sesión de análisis posterior).

- [ ] **Step 6: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: agregar flag --period {week,month} y generalizar reporte/CSV

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Verificación final contra el spec

- `split_periods()` con soporte week/month, mismo shape de retorno → Task 1.
- Renombrado genérico `weekly`→`periods`, `train_weeks`→`train_periods`, `period` threadeado por `run_analysis()` → Task 2.
- CLI `--period`/`--train-periods`, reporte con palabra genérica "período" (corrección de diseño documentada), CSV con columnas `period_*` → Task 3.
- Fuera de alcance respetado: no se toca `optimize.py`, `tradebot.py`, `backtest.py`; no se migran CSVs/logs viejos; no se agregan otras granularidades.
- Corrida real con `--period month` sobre datos ya cacheados → Task 3, Step 5.
