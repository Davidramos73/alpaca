# Granularidad de período configurable (semana/mes) en walk_forward.py — Diseño

**Fecha:** 2026-07-05
**Proyecto:** `invertirCarlos/alpaca`
**Objetivo:** permitir correr el análisis de estabilidad y el torneo de `walk_forward.py` con períodos mensuales además de semanales, para comparar si la cadencia de re-optimización mensual muestra más persistencia (autocorrelación, menor regret) que la semanal — la hipótesis original del usuario era que el % óptimo "varía mes a mes, semana a semana, día a día", y hasta ahora solo se validó la cadencia semanal (con resultado: sin persistencia, ver `docs/walk-forward-log.md`).

## Contexto

`walk_forward.py` parte el histórico en semanas ISO (`split_weeks()`) y todo el resto del pipeline (`run_grid`, `select_peak`, `select_plateau`, `lag1_corr`, `median_params`, `regret_series`, `simulate_adaptive`, `tournament`, `run_analysis`, `build_report`) opera sobre una lista genérica de "períodos" sin asumir en ningún lado que duran 7 días — la única atadura a "semana" está en el nombre de `split_weeks()`, en los nombres de parámetros (`weekly`, `week_dfs`, `train_weeks`, etc.) y en el texto fijo del reporte/CSV.

## Alcance

Generalizar la granularidad de período a `{week, month}` mediante un flag CLI, reutilizando el pipeline existente sin duplicarlo. Sin cambios en `optimize.py`, `simulate()` ni `new_state()`.

## Cambios

### 1. Partición de períodos

`split_weeks(df_1min)` se renombra a `split_periods(df_1min, period)`, donde `period` es el string `"week"` o `"month"`:

- `"week"`: comportamiento idéntico al actual (agrupa por `(iso["year"], iso["week"])`, label `"{year}-W{week:02d}"`).
- `"month"`: agrupa por `(timestamp.dt.year, timestamp.dt.month)`, label `"{year}-M{month:02d}"`.

Ambos devuelven la misma forma: `list[dict]` con claves `label`, `start`, `end`, `df` (df con índice reseteado), en orden cronológico, omitiendo períodos sin velas, incluyendo períodos parciales al inicio/fin del rango tal cual (mismo comportamiento que hoy para semanas). Ambos usan la misma columna `timestamp` (UTC, tz-aware) sin conversión adicional — a diferencia del año ISO de las semanas, el mes calendario de un timestamp no tiene ambigüedad de frontera.

### 2. Renombrado genérico de parámetros

Sin cambios de lógica, solo de nombres, en las funciones que hoy reciben la lista de períodos o el contador de entrenamiento:

| Antes | Después |
|---|---|
| `weekly: list[dict]` (parámetro en `regret_series`, `tournament`, `run_analysis`) | `periods: list[dict]` |
| `week_dfs: list[pd.DataFrame]` (en `simulate_adaptive`) | `period_dfs: list[pd.DataFrame]` |
| `params_per_week` (en `simulate_adaptive`) | `params_per_period` |
| `train_weeks: int` (en `tournament`, `run_analysis`, CLI) | `train_periods: int` |

El cuerpo de cada función no cambia — ya iteran genéricamente sobre la lista sin asumir tamaño de período.

### 3. CLI

`main()` agrega:

```
--period {week,month}   Granularidad de los períodos (default: week)
```

y renombra `--train-weeks` a `--train-periods` (mismo default `1`, misma validación `< 1` → `parser.error`).

**Corrección respecto al código real:** `split_weeks()` no se llama desde `main()` — se llama dentro de `run_analysis()`. Por lo tanto `run_analysis()` gana un nuevo parámetro `period: str = "week"` y es quien llama a `split_periods(df_1min, period)` internamente; `main()` simplemente pasa `period=args.period` a `run_analysis()`.

### 4. Reporte y CSV

**Corrección de diseño (detectada al planificar):** la idea original de alternar la palabra "semana"/"mes" en cada título del reporte choca con la concordancia de género del español — "semana" es femenino ("la semana anterior", "semanas aisladas") y "mes" es masculino ("el mes anterior", "meses aislados"). Un simple `if/else` de palabra sin ajustar artículos/adjetivos produce texto gramaticalmente incorrecto para uno de los dos casos. En vez de mantener una tabla de formas por género (complejidad innecesaria para un reporte de uso personal), se usa la palabra genérica **"período"** (masculino, sin ambigüedad de concordancia) en todos los títulos y prosa del reporte — funciona igual para semanas o meses sin ningún condicional de texto. La granularidad realmente usada se comunica de otra forma: (a) un campo explícito `granularidad: {period}` en la cabecera del log, y (b) las etiquetas de cada fila (`2026-W03` vs `2026-M03`) ya distinguen semana de mes sin ambigüedad. Ejemplos concretos del cambio de texto:
  - `"1) ESTABILIDAD DE LOS ÓPTIMOS SEMANALES"` → `"1) ESTABILIDAD DE LOS ÓPTIMOS POR PERÍODO"`
  - `"REGRET (usar el óptimo de la semana anterior vs el propio, semanas aisladas)"` → `"REGRET (usar el óptimo del período anterior vs el propio, períodos aislados)"`
  - `"PARÁMETROS USADOS POR SEMANA"` → `"PARÁMETROS USADOS POR PERÍODO"`
  - encabezado de columna `"semana"` → `"período"`
  - `f"(n = {len(weekly)} semanas: muestra chica...)"` → `f"(n = {len(periods)} períodos: muestra chica...)"`
  - `f"{len(weekly) - train_weeks} semanas de aplicación"` → `f"{len(periods) - train_periods} períodos de aplicación"`
  - `f"Semanas: {len(weekly)} (...)"` (cabecera) → `f"Períodos: {len(periods)} (...)   |   granularidad: {period}   |   ..."`

  El mismo criterio aplica al mensaje de error de `run_analysis()` cuando faltan datos: `f"Error: {len(weeks)} semana(s) de datos..."` → `f"Error: {len(periods_list)} período(s) de datos..."`.
- CSV: columnas `week_label/week_start/week_end` pasan a `period_label/period_start/period_end` (nombres genéricos, válidos para ambas granularidades). Los CSVs ya generados con el esquema viejo (`walkforward_TSLA_*.csv`, `walkforward_SPCX_*.csv` existentes) no se migran ni se regeneran — quedan como están, documentados con su esquema en `docs/walk-forward-log.md`.

### 5. Nombre de archivo de salida

Sin cambios: sigue siendo `walkforward_{symbol}_{timestamp}.{log,csv}` independientemente del período elegido (el `--period` usado queda documentado dentro del log, en la cabecera junto a `train_periods` e `intervalos`).

## Testing (TDD, datos sintéticos)

1. **`split_periods(df, "week")` reproduce exactamente el comportamiento actual** — los tests existentes de `split_weeks` se migran a llamar `split_periods(df, "week")` con las mismas aserciones (ISO, huecos, parciales).
2. **`split_periods(df, "month")` con un caso que cruce fin de mes**: timestamps sintéticos en enero (2 tramos) y febrero (1 tramo, con un hueco de días en el medio), análogo al test de huecos/parciales de semanas — verificar labels `"2026-M01"`/`"2026-M02"`, conteo de velas por mes, orden cronológico.
3. **`run_analysis()` end-to-end con `period="month"`** sobre datos sintéticos de al menos 3 meses (reutilizando el patrón de `_weekly_sintetico`, generalizado a un helper que acepte el período) — confirma que el pipeline completo (estabilidad, regret, torneo, veredicto) corre sin asumir semanas.
4. Tests existentes que llaman funciones renombradas (`tournament`, `simulate_adaptive`, `regret_series`, `run_analysis`) se actualizan para pasar los parámetros con los nuevos nombres — sin cambios de aserciones, ya que la lógica no cambia.
5. CLI: test de humo (subprocess o llamada directa a `main()`) verificando que `--period month` no crashea y que `--train-periods 0` sigue rechazándose limpiamente (mismo test que ya existe para `--train-weeks`, adaptado al nuevo nombre de flag).

## Fuera de alcance (YAGNI)

- Otras granularidades (diaria, trimestral, bimestral) — se agregarían después con el mismo patrón (`split_periods` ya está preparado para eso) si hiciera falta.
- Migración de CSVs/logs ya generados al esquema de columnas nuevo.
- Cambios en `tradebot.py`, `backtest.py`, `optimize.py`.
- Comparar automáticamente semana vs. mes en una sola corrida — cada corrida sigue siendo de una sola granularidad; la comparación semanal-vs-mensual se hace corriendo el script dos veces y anotando ambos resultados en `docs/walk-forward-log.md` (como ya se hizo para TSLA 6 meses vs 1 año).

## Criterio de éxito

Correr `python3 walk_forward.py --symbol TSLA --date-start 2025-07-01 --date-end 2026-07-01 --intervals 20 --period month --train-periods 1` sobre el caché ya existente (`cache_TSLA_20250701_20260701_1Min.pkl`, sin red) y obtener un veredicto mensual comparable al semanal ya registrado en la bitácora, para responder si la cadencia mensual muestra más persistencia que la semanal.
