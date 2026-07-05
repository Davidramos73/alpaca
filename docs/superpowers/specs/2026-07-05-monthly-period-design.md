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

`main()` llama a `split_periods(df_1min, args.period)` en vez de `split_weeks(df_1min)`.

### 4. Reporte y CSV

- Texto del log: dondequiera que hoy diga literalmente "SEMANA" en `build_report()`, se calcula `period_word = "semana" if period == "week" else "mes"` una vez al principio de la función y se usa en los títulos/encabezados ("PARÁMETROS USADOS POR {PERIOD_WORD}", encabezado de columna, sección de estabilidad, sección de regret).
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
