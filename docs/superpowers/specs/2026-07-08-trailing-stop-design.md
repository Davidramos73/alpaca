# Trailing Stop ("mantener y cabalgar") — Diseño

**Fecha:** 2026-07-08
**Proyecto:** `invertirCarlos/alpaca`
**Objetivo:** Medir con datos históricos si reemplazar la venta inmediata del grid por un trailing stop con seguimiento ("mantener y cabalgar") mejora los resultados, antes de tocar el bot en vivo. Resuelve la queja de que en crecidas grandes el grid vende demasiado pronto.

## Contexto

La estrategia grid actual (`tradebot.py` / `backtest.py` / `optimize.py`): compra `BUY_AMOUNT` cuando el precio cae `buy_drop_pct` desde la última compra, vende la última posición (LIFO) cuando sube `sell_rise_pct`, máximo 10 posiciones, con pool de reinversión de ganancias. La referencia para decidir comprar/vender es **siempre la última compra** (tope de la pila): `buy_target = tope×(1−drop)`, `sell_target = tope×(1+rise)`. El precio de venta nunca se vuelve referencia.

El problema observado: en una subida sostenida el grid escalona salidas temprano (vende el tope al +rise%, luego el siguiente al +rise%, etc.), lo que **corta la ganancia** en un rally grande.

La idea: cuando el tope llega a `sell_target`, en vez de vender, armar un trailing stop que sigue el pico del precio y solo vende cuando el precio retrocede `trail_pct` desde ese pico. Alpaca no ofrece trailing stop nativo utilizable acá, así que lo implementamos nosotros. Requiere consultar precio más seguido que el intervalo de decisión para ejecutar el stop a tiempo.

## Alcance

- **Solo backtest.** No se toca `tradebot.py` en este trabajo.
- Rama nueva `feature/trailing-stop` desde `main` (base limpia, sin los mecanismos anti-crash de `feature/risk-mechanisms`).
- Entregable: poder correr `optimize.py` y ver una tabla comparativa de ROI vanilla vs. ROI con trailing, barriendo unos pocos valores de `trail_pct` sobre la mejor combinación.
- **Fuera de alcance** (futuro, si el número da bien): portar a `tradebot.py`, integrar el trailing al plot/JSON de equity, buscar la mejor combinación *bajo* trailing (no solo aplicar trailing a la mejor combo vanilla).

## Mecanismo: máquina de dos estados

`simulate_trailing()` recorre **todas** las velas de 1 minuto. Un checkpoint de decisión ocurre cada `interval_minutes`-ésima fila (misma alineación exacta que el `df.iloc[::interval_minutes]` de vanilla, para consistencia). Usa solo el `close` de cada vela de 1 min (decisión tomada en la sección "cómo testear").

**Estado NORMAL** (mirando el lote tope; se evalúa solo en checkpoints):
- Pila vacía → compra inicial al precio actual (idéntico a vanilla).
- Precio ≤ `buy_target` y `len(purchases) < max_buys` → **compra** (nuevo lote tope).
- Precio ≥ `sell_target` → **no vende**: transiciona a TRAILING con `peak = precio`, `stop = peak×(1−trail_pct)`, `sell_target_ref = sell_target` (guardado solo para la métrica de reporte).

**Estado TRAILING** (siguiendo el precio en cada vela de 1 min, no solo checkpoints):
- `peak = max(peak, precio)`; `stop = peak×(1−trail_pct)`.
- Cuando un `close` ≤ `stop` → **vende el lote tope al precio real de ese close**, vuelve a NORMAL. La referencia pasa automáticamente a la compra anterior (o pila vacía → compra inicial en el próximo checkpoint).
- Mientras cabalga **no compra** (el stop siempre queda muy por encima del `buy_target`, así que en la práctica no se dispararía igual; se suspende explícitamente por simplicidad).
- Si la serie de datos termina con un trailing activo, se **liquida al último `close`** disponible.

En la vela de armado, `peak = precio` y `stop = precio×(1−trail_pct) < precio`, así que el stop no se dispara en esa misma vela (con `trail_pct > 0`): el seguimiento arranca efectivamente en la vela siguiente.

## Contabilidad

Todo usa el **precio real de ejecución** (efectivo, ganancia, `profit_pool`, drawdown). Es la estrategia real, es plata real y disponible de verdad para reinvertir.

- Ganancia de la venta = `(qty×exec − sell_fee) − (effective_buy + buy_fee)`, con `exec` = precio real del close donde disparó el stop. Si `> 0` se suma al `profit_pool` como hoy.
- **Métrica de reporte (no afecta efectivo):** por cada venta con trailing, `trailing_capture = qty×(exec − sell_target_ref)`. Cuánto sumó (o restó) cabalgar vs. haber vendido apenas se tocó `rise%`. Puede ser negativo si el precio cae apenas armado el stop (venta por debajo de `sell_target`). Se acumula un total.

La referencia para futuras decisiones es siempre la compra anterior, así que el precio de venta (real o `sell_target`) no controla ninguna decisión posterior — solo la métrica de reporte.

## `simulate_trailing()`

Función nueva en `optimize.py`, al lado de `simulate()`:

```python
def simulate_trailing(df_1min, max_buys, buy_drop_pct, sell_rise_pct, fee_pct,
                      use_pool=True, buy_amount=BUY_AMOUNT, interval_minutes=1,
                      trail_pct=0.0, on_trade=None, on_bar=None) -> dict
```

- Recibe el df de 1 minuto completo (no resampleado) + `interval_minutes` (cadencia de checkpoints) + `trail_pct`.
- Devuelve el **mismo dict que `simulate()`** (roi, profit, total_equity, buys, sells, open_positions, etc.) más las claves de trailing: `trailing_capture_total`, `trailing_sells` (cuántas ventas salieron por stop), y opcionalmente `trailing_captures` (lista por venta) para inspección/test.
- `simulate()` vanilla queda **intacto** y sigue siendo el baseline de comparación.

## CLI e integración en `optimize.py`

Flag nuevo: `--trail-pcts "0.5,1,1.5,2"` (lista de porcentajes separada por coma, estilo `--intervals`; cada valor se interpreta como % y se divide por 100). Requiere un solo `--intervals` (igual que `--export-equity-json`).

Cuando se pasa `--trail-pcts`:
1. Corre el grid search vanilla normal → mejor combinación (drop/rise/interval). Sin cambios en este paso.
2. Para cada `trail_pct` de la lista, corre `simulate_trailing` con esa mejor combo sobre las velas de 1 min.
3. Imprime (y agrega al log) una tabla comparativa:

```
  COMPARACIÓN TRAILING STOP (mejor combo: drop D% / rise R% / interval M min)
--------------------------------------------------------------------------------
  ESTRATEGIA        ROI        maxDD     Compras  Ventas  Trailing capture
  vanilla           +9.06%     35.01%    38       29      —
  trail 0.5%        +X.XX%     XX.XX%    XX       XX      +$YYY
  trail 1.0%        +X.XX%     XX.XX%    XX       XX      +$YYY
  ...
```

El baseline vanilla es la mejor combo del grid search (ya calculada). La comparación directa ROI-vanilla vs. ROI-trailing muestra el efecto neto; `trailing capture` muestra cuánto de la diferencia vino de cabalgar.

Nota: bajo trailing, compras/ventas pueden diferir de vanilla (se mantiene más tiempo, se escalona distinto) — por eso se muestran esas columnas.

## Testing

`test_trailing.py` nuevo (pytest, DataFrames a mano con columnas `timestamp` y `close`, `interval_minutes` chico —ej. 2 o 3— para tener checkpoints seguidos):

1. **Cabalga y retrocede:** compra@100, checkpoint arma en ≥`sell_target`, el precio sube (ej. 105→110), luego un close cae por debajo de `110×(1−trail)` → vende ahí. Verifica precio de venta, ganancia y `trailing_capture` positivo.
2. **Caída inmediata (capture negativo):** arma el stop, el siguiente close cae bajo el stop por debajo de `sell_target` → vende ahí, `trailing_capture` negativo.
3. **Nunca dispara:** el precio sube monótono hasta el final → liquida al último `close`.
4. **Recompra tras salir:** después de vender en el retroceso, el precio sigue cayendo hasta `buy_target` → compra un nuevo lote.
5. **Mantener con varios lotes:** dos lotes abiertos, el precio sube; verifica que **no** vende el lote de abajo durante la subida (mantiene), solo el tope al retroceder.
6. **Forma del retorno:** `simulate_trailing` devuelve las claves esperadas (las de `simulate()` + las de trailing).

## Riesgos y decisiones registradas

- **Modelo "mantener y cabalgar" cambia la estrategia, no es un overlay.** El timeline difiere de vanilla a propósito (mantiene más, escalona distinto). Es lo buscado: no malvender temprano en crecidas grandes.
- **`close` de 1 min** puede perderse picos/valles intra-minuto; aceptado para una primera medición del efecto.
- **`trail_pct=0`** no reproduce vanilla exacto (arma y vende una vela fina después); vanilla se mide con `simulate()`, no con `simulate_trailing(trail_pct=0)`.
- El backtest mide el potencial de la estrategia; el bot en vivo tendría slippage y timing de ejecución reales (fuera de alcance acá).
