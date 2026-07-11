# Estrategia double trailing (compra y venta con trailing)

## Contexto

El proyecto compara variantes de grid trading por etapas: `strategies/vanilla/`
(rangos fijos drop/rise), `strategies/trailing/` (trailing solo en la venta).
Esta tercera etapa agrega trailing también en la compra: al caer el precio
`drop%` no se compra de inmediato — se sigue el mínimo vela a vela y se compra
recién cuando rebota `trail_buy%` desde ese mínimo. La venta usa el mismo
trailing que la etapa anterior (pico + retroceso `trail_sell%`).

Ejemplo (drop 2%, trail_buy 1%): el precio cae 2% → se arma el trailing de
compra; sigue cayendo a −3%, −4% → el piso acompaña; rebota 1% desde el
mínimo → compra ahí. Si en cambio hubiera rebotado apenas tocó −3%, compraba
al volver a ~−2%.

## Decisiones tomadas

- **Venta con trailing** (no vanilla): la estrategia es trailing en ambas puntas.
- **trail_buy y trail_sell independientes**, cada uno con su propia lista de
  valores a probar; el optimizer encuentra la mejor combinación.
- **Grilla completa 4D en una sola fase**: drop 1–10% × rise 1–10% ×
  trail_buy × trail_sell (defaults `0.5,1,1.5` para ambos → 900
  simulaciones sobre velas de 1 minuto). No se usa el patrón 2-fases de
  trailing (mejor combo vanilla + trails encima): el usuario prefiere el
  óptimo global aunque tarde más.
- **Primera compra inmediata** (BUY_INIT en la primera vela, como vanilla y
  trailing). El trailing de compra aplica solo a las compras de grid.
- Nombre y ubicación: `strategies/double_trailing/` con su `optimize.py` y
  su `viewer/` (mismo patrón que las etapas anteriores).

## Simulación — `simulate_double_trailing()`

Nueva función en `strategies/double_trailing/optimize.py`, derivada de
`simulate_trailing()` de `strategies/trailing/optimize.py`:

- Compra inicial inmediata en la primera vela.
- Gatillo de compra de grid: `price <= last_price * (1 - drop_pct)` arma el
  trailing de compra `{"valley": price, "arm": price * (1 + trail_buy_pct)}`.
  Vela a vela: si `price < valley`, bajan `valley` y `arm`; si
  `price >= arm`, se ejecuta la compra al precio actual (BUY_GRID),
  respetando `max_buys`.
- Gatillo de venta: `price >= last_price * (1 + rise_pct)` arma el trailing
  de venta `{"peak", "stop"}`, idéntico al de `simulate_trailing()`: sigue el
  pico y vende (LIFO, la última posición) cuando `price <= stop`.
- Mientras cualquiera de los dos trailings está armado no se evalúan los
  gatillos del grid (son mutuamente excluyentes al armarse: uno exige precio
  bajo el target de compra, el otro sobre el de venta).
- Fin de datos: con trailing de venta armado se liquida esa posición al
  último close (como trailing); con trailing de compra armado simplemente no
  se compra.
- Sin cambios en el resto de la mecánica: `MAX_BUYS = 10` fijo, pool de
  ganancias (`use_pool`), fees, precio real de ejecución en la contabilidad,
  seguimiento vela a vela (requiere histórico de 1 minuto completo).
- Métricas de reporte: además de las de `simulate_trailing()`
  (`trailing_capture_total`/`trailing_sells` para la venta), la compra
  registra `buy_capture_total` = Σ qty × (precio_de_armado − precio_pagado):
  cuánto se ahorró (o perdió) por esperar el rebote vs. comprar al tocar el
  drop. Puede ser negativo si el rebote deja el precio por encima del punto
  de armado.

## Optimizer — `strategies/double_trailing/optimize.py`

- CLI: mismos flags base que trailing (`--symbol`, `--date-start`,
  `--date-end`, `--buy-amount`, `--fee-pct`, `--export-equity-json`,
  `--out-dir`) más `--trail-buy-pcts` y `--trail-sell-pcts` (listas separadas
  por coma, default `0.5,1,1.5` cada una). Intervalo fijo 1 minuto: no hay
  flag `--intervals`.
- Corre la grilla 4D completa con `simulate_double_trailing()` y además la
  grilla vanilla 2D (drop × rise con `simulate()` importado con la misma
  técnica de aislamiento de módulos) para la fila de referencia.
- Log y CSV con el ranking completo bajo `logs/` (mismo patrón que trailing).
- Export JSON por corrida en `out_dir/<SYMBOL>/`:
  - `price` diario (una sola vez, compartido),
  - top N combos (N=20), cada uno con sus métricas de tabla **y sus series**
    (`equity` diaria y `trades` con `time`/`buy_time`, tipos
    BUY_INIT/BUY_GRID/SELL, `trailing_capture` y `buy_capture` por trade
    cuando aplique) — las series diarias son livianas, así el viewer puede
    graficar cualquier fila del top 20,
  - la fila vanilla de referencia (mejor drop/rise sin trailing) con su
    `equity` diaria y `best_trades`, para la comparación indexada.
- `regenerate_manifest(out_dir)` propio (una entrada por corrida; sin la
  estructura base+trail_files de trailing, acá es un solo JSON por corrida).
- Errores con `sys.exit(1)` (mismo criterio que trailing tras su Task 2).

## Viewer — `strategies/double_trailing/viewer/`

Clon del patrón de `strategies/trailing/viewer/` (React 19 + Vite, sin
TypeScript, mismos middlewares dev):

- `run-optimize`: body `{symbol, date_start, date_end, buy_amount, fee_pct,
  trail_buy_pcts, trail_sell_pcts}`; el middleware agrega siempre
  `--export-equity-json --out-dir viewer/public/data` (sin campo de
  intervalo ni max_buys).
- `delete-run`: borra el JSON de la corrida y regenera el manifest.
- UI:
  - Tabla top ~20: drop, rise, trail_buy, trail_sell, ROI, ganancia,
    compras, ventas, buy_capture, trailing_capture — ordenada por ROI desc,
    con la fila "vanilla (mejor drop/rise)" como referencia.
  - Al seleccionar cualquier fila del top 20: chart de precio + trades y
    chart de equity indexada vs. vanilla, reutilizando
    `TrailingTradesChart`/`IndexedEquityChart` adaptados.
  - Form de nueva corrida con los campos del body de `run-optimize`.
- Utilidades compartidas (`chartMath.js`, `useZoom.js`, `useTooltip.js`,
  `ChartFrame.jsx`, `Tooltip.jsx`, `index.css`) copiadas de trailing (que a
  su vez son las de vanilla), incluyendo el fix de alineación de markers
  post-zoom ya aplicado en trailing.

## Tests

`strategies/double_trailing/test_optimize.py`, cargando el módulo por
`importlib.util.spec_from_file_location` (nunca `from optimize import ...`,
por la colisión de nombres documentada en el plan de trailing). Casos TDD
para `simulate_double_trailing()` con series sintéticas:

1. Caída que arma el trailing de compra y sigue cayendo sin rebote → no
   compra; el valley acompaña el mínimo.
2. Caída + rebote ≥ trail_buy% → compra al precio del rebote (no al del
   drop), y `buy_capture` refleja la diferencia.
3. Subida + retroceso → vende vía trailing de venta (comportamiento heredado,
   verificado en el contexto nuevo).
4. Ciclo completo compra-con-rebote → venta-con-retroceso: contabilidad
   (cash, qty, profit) exacta de punta a punta.
5. Trailing de compra armado al final de los datos → no compra, sin errores.
6. `regenerate_manifest()` para el manifest de una entrada por corrida.

## Fuera de alcance

- No se modifican `strategies/vanilla/` ni `strategies/trailing/`.
- No se toca `simulate()` ni `simulate_trailing()` originales.
- Charts para combos fuera del top 20 de cada corrida (el JSON solo trae
  series del top N).
- Trailing en la primera compra (decidido: compra inmediata).
- `--max-buys` configurable (sigue fijo en 10).
