# Viewer para la estrategia trailing

## Contexto

`strategies/vanilla/viewer/` ya provee una app React (Vite) que lee JSON de
equity generados por `optimize.py --export-equity-json` y permite lanzar
nuevas corridas desde un form. Queremos el equivalente para la estrategia
trailing, con una diferencia obligatoria: el trailing stop necesita
seguimiento vela a vela del precio para detectar el pico correctamente, así
que el intervalo de revisión **siempre** debe ser 1 minuto — no es
configurable desde la UI.

Una corrida de trailing produce, además del JSON "vanilla" (grid drop/rise,
igual al que ya usa el viewer de vanilla), un JSON adicional por cada
`trail_pct` comparado (`--trail-pcts 0.5,1,1.5,2` → 4 JSON extra). El viewer
debe mostrar esto como una comparación lado a lado: vanilla (mejor combo
drop/rise) vs. cada porcentaje de trailing.

## Alcance

1. `strategies/trailing/optimize.py`: agregar `--out-dir` + manifest de
   runs agrupados.
2. Nueva app `strategies/trailing/viewer/` (proyecto Vite independiente,
   igual patrón que `vanilla/viewer/`).
3. Borrar `plot_trailing.py` y `run_trail.sh` (raíz y
   `strategies/trailing/`) una vez el viewer los reemplace. `run_trail.sh`
   ya está roto hoy (llama a `plot_equity.py`, que no existe desde el
   refactor a `strategies/`).
4. Actualizar `README.md` para describir el nuevo viewer y quitar las
   referencias a los scripts borrados.

Fuera de alcance: no se modifica la lógica de simulación
(`simulate`/`simulate_trailing`), ni el viewer de vanilla.

## Backend — `strategies/trailing/optimize.py`

- `LOGS_DIR = "logs"`: mover `cache_*.pkl`, `.log` y `.csv` ahí (mismo
  patrón que vanilla), en vez de escribirlos en el cwd.
- `--out-dir` (default `viewer/public/data`): el JSON base y cada
  `_trail_<pct>_equity.json` se escriben en `out_dir/<SYMBOL>/`.
- `--intervals` deja de aceptarse como lista para forzar 1 minuto: el CLI
  sigue aceptando el flag por compatibilidad con usos manuales existentes,
  pero el form del viewer nunca lo manda (el body POST no tiene ese campo,
  el middleware siempre agrega `--intervals 1`).
- Nueva `regenerate_manifest(out_dir)` — distinta a la de vanilla porque
  agrupa varios archivos en un solo "run":

  ```json
  [
    {
      "run_ts": "20260710_193000",
      "symbol": "TSLA",
      "date_start": "2026-01-01",
      "date_end": "2026-06-28",
      "base_file": "TSLA/optimize_TSLA_20260710_193000_equity.json",
      "trail_files": [
        {"trail_pct": 0.5, "file": "TSLA/optimize_TSLA_20260710_193000_trail_0.5_equity.json"},
        {"trail_pct": 1.0, "file": "TSLA/optimize_TSLA_20260710_193000_trail_1.0_equity.json"}
      ]
    }
  ]
  ```

  Se construye escaneando `out_dir/<symbol>/*_equity.json`, separando por
  regex el `run_ts` y si el nombre tiene `_trail_<pct>_` o no, y agrupando
  por `(symbol, run_ts)`. Solo entran runs con `base_file` presente (si un
  run quedó a medias, se ignora).
- Cada trail JSON conserva su forma actual (`price`, `equity`, `trades`,
  `roi`, `profit`, `total_equity`, `total_fees`, `buys`, `sells`,
  `open_positions`, `trailing_capture_total`, `trailing_sells`,
  `buy_drop_pct`, `sell_rise_pct`, `trail_pct`) — sin cambios.
- El JSON base conserva su forma actual (`price`, `series`, `best_combo`,
  `best_trades`, etc.) — sin cambios; el viewer solo usa `price`,
  `best_combo` y `best_trades` de ahí (ignora `series`, que es el grid
  completo de combos, no relevante para esta vista).

## `strategies/trailing/viewer/` (proyecto Vite nuevo)

Mismo esqueleto que `vanilla/viewer/` (React 19 + Vite, sin TypeScript):

- `vite.config.js`: mismas dos middlewares (`run-optimize`, `delete-run`),
  apuntando a `strategies/trailing/optimize.py` vía el mismo
  `VENV_PYTHON` (`../../../../.venv/bin/python` relativo a
  `strategies/trailing/viewer/`).
- `run-optimize`: valida body `{symbol, date_start, date_end, buy_amount,
  fee_pct, trail_pcts}`. `trail_pcts` es un string con números positivos
  separados por coma (regex `^\d+(\.\d+)?(,\d+(\.\d+)?)*$`, al menos un
  valor). El spawn de `optimize.py` siempre incluye
  `--intervals 1 --export-equity-json --trail-pcts <trail_pcts> --out-dir viewer/public/data`
  — no hay campo de intervalo ni de max_buys en el body.
- `delete-run`: recibe `{run_ts, symbol}`. Resuelve `base_file` +
  `trail_files` desde el manifest actual, borra esos archivos (validando
  que caigan dentro de `DATA_DIR`, igual que vanilla), regenera manifest.
- `public/data/.gitkeep` + `public/data/.gitignore` (`*` / `!.gitignore` /
  `!.gitkeep`) igual que vanilla, para no commitear corridas de ejemplo.
- Puerto: el default de Vite (5173) puede chocar si vanilla ya está
  corriendo — se deja el default; si el puerto está ocupado, Vite prueba
  el siguiente automáticamente (comportamiento de serie, no requiere
  configuración).

### Componentes React

- `RunForm.jsx`: campos Símbolo, Desde, Hasta, Monto por compra ($), Fee
  (%), Trailing % a comparar (texto libre, default `"0.5,1,1.5,2"`,
  validado con la misma regex del backend antes de enviar). Sin campo de
  intervalo ni de compras máximas (fijo en 10 en el backend, no
  configurable, no se muestra).
- `App.jsx`: dropdown de runs (label `symbol · fechas · run_ts`, igual que
  vanilla) sobre el manifest agrupado. Al elegir un run, fetch de
  `base_file` + todos los `trail_files`.
- `ComparisonTable.jsx` (nuevo): fila "vanilla" (del `base_file`: ROI,
  profit, buys, sells, trailing_capture `—`) + una fila por trail_pct
  (ROI, profit, buys, sells, trailing_capture_total). Ordenadas por ROI
  desc, igual que la tabla que ya imprime el log de `optimize.py`.
- Selector de serie a inspeccionar (tabs): "Vanilla" + un tab por
  trail_pct presente. Default: el de mayor ROI (vanilla o algún trailing).
- `TrailingTradesChart.jsx` (adaptado de `TradesChart.jsx` de vanilla):
  igual esqueleto de gráfico de precio + marcadores, pero reconoce tipos
  `BUY_INIT`/`BUY_GRID` (compra) además de `BUY`, y en el tooltip de venta
  agrega `trailing_capture` cuando el trade lo tenga.
- `IndexedEquityChart.jsx` (nuevo, reemplaza a `Top5Chart` para este
  caso): superpone la curva de equity de "vanilla" (base_file, ganancia
  diaria del `best_combo`) vs. la curva de equity de la serie trailing
  seleccionada, ambas indexadas a 100 en el primer punto, para visualizar
  la divergencia. Nota: el `base_file` no trae una curva de equity diaria
  del *best combo* aislada — trae `series` (grid completo). Para obtener
  la curva vanilla aislada, se toma de `series` la entrada cuyo
  `drop_pct`/`rise_pct` coincide con `best_combo` (siempre está presente,
  ya que `best_combo` se deriva del mejor resultado del grid).
- Reusa de vanilla (copiados tal cual, sin lógica de negocio propia):
  `Tooltip.jsx`, `useTooltip.js`, `useZoom.js`, `chartMath.js`,
  `ChartFrame.jsx`, `index.css` (mismo look & feel).

## Limpieza post-viewer

Una vez el viewer de trailing esté funcionando y verificado:

- Borrar `plot_trailing.py` y `run_trail.sh` en la raíz (shims) y en
  `strategies/trailing/`.
- Actualizar `README.md`: describir `strategies/trailing/viewer/` igual
  que ya se describe `strategies/vanilla/viewer/`, quitar las menciones a
  `plot_trailing.py`/`run_trail.sh`.

## Fuera de alcance

- No se cambia `simulate()` / `simulate_trailing()`.
- No se toca el viewer de vanilla.
- No se agrega `--max-buys` configurable a trailing (sigue fijo en 10).
- No se arregla `run_trail.sh` antes de borrarlo (ya está roto, no vale la
  pena parchear algo que se va a eliminar).
