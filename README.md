# Bot de Grid Trading con Alpaca

Este proyecto implementa un robot de trading algorítmico escrito en Python que ejecuta una estrategia de **Grid Trading (Cuadrícula LIFO)** usando la API de **Alpaca** (Paper Trading). Además del bot en vivo, incluye herramientas para simular la estrategia sobre datos históricos, optimizar sus parámetros y analizar qué tan estables son esos parámetros óptimos en el tiempo.

---

## 📈 Lógica de la Estrategia

1. **Partición de Capital**: divide el capital disponible en bloques de `BUY_AMOUNT` (default $10,000). El bot acumula como máximo `MAX_BUYS` compras simultáneas (default 10).
2. **Inicio del Grid**: al arrancar sin estado previo, realiza una compra a mercado inicial para fijar el primer precio de referencia.
3. **Pila de Memoria (LIFO)**: las compras se guardan en orden secuencial (como una pila). La última compra realizada define los límites actuales del bot.
4. **Compra por Caída**: si el precio cae `BUY_DROP_PCT` o más respecto al precio de la última compra de la pila (y hay cupo), el bot compra y apila.
5. **Venta por Subida**: si el precio sube `SELL_RISE_PCT` o más respecto al precio de la última compra, el bot vende esa posición (Pop) y el precio de referencia vuelve a la compra anterior de la pila.
6. **Pool de reinversión** (opcional, activado por default): las ganancias netas de cada venta se acumulan en un pool y se reparten como bonus entre las compras siguientes, en vez de quedar ociosas en cash.

Estos tres parámetros (`BUY_DROP_PCT`, `SELL_RISE_PCT`, el intervalo de consulta) son justamente lo que `optimize.py` y `walk_forward.py` ayudan a calibrar.

---

## 📁 Estructura de Archivos

* **`tradebot.py`** — Bot de trading en vivo (o paper trading). Genérico y configurable por variables de entorno o flags CLI; corre en un loop infinito con `--interval` segundos entre consultas. Este es el script que corre en Docker.
* **`backtest.py`** — Simulación histórica de la estrategia sobre datos reales descargados de Alpaca, con reporte de ROI, ganancia y operaciones.
* **`optimize.py`** — Grid search: prueba todas las combinaciones de `buy_drop_pct` (1–10%) × `sell_rise_pct` (1–10%) × los intervalos indicados sobre un rango histórico completo, y reporta la mejor combinación por ROI.
* **`walk_forward.py`** — Análisis por período (semana o mes, según `--period`): mide qué tan estables/predecibles son los parámetros óptimos de un período a otro (dispersión, autocorrelación, *regret*) y corre un torneo que compara parámetros fijos contra distintas variantes de auto-ajuste periódico, para decidir con datos si vale la pena adaptar el bot en vivo. Ver `docs/superpowers/specs/2026-07-04-walk-forward-design.md` (diseño original, semanal) y `docs/superpowers/specs/2026-07-05-monthly-period-design.md` (agregado de granularidad mensual) para el diseño completo, y `docs/walk-forward-log.md` para la bitácora de hallazgos.
* **`tesla.py`** — Prototipo original del bot, con la lógica y los parámetros hardcodeados para TSLA. Reemplazado por `tradebot.py`; se conserva solo como referencia histórica.
* **`test1.py`** — Script suelto de prueba para consultar precios de Alpaca. No forma parte del flujo principal.
* **`test_walk_forward.py`** — Suite de tests (pytest) para las funciones de `optimize.py` y `walk_forward.py`, con datos sintéticos (no requiere red ni credenciales).
* **`requirements.txt`** — Dependencias del proyecto (`alpaca-py`, `python-dotenv`, `pandas`).
* **`.env.example`** — Plantilla de variables de entorno; copiar a `.env` y completar con credenciales reales.
* **`Dockerfile` / `docker-compose.yml`** — Empaquetan y corren `tradebot.py` en un contenedor.

Archivos generados en tiempo de ejecución (ignorados por git, ver `.gitignore`): `tradebot_<symbol>.log`, `*_state.json` (estado persistente del bot), `cache_*.pkl` (caché de velas históricas), `backtest_*.log`, `optimize_*.csv`/`.log`, `walkforward_*.csv`/`.log`.

---

## 🛠️ Configuración e Instalación

### 1. Requisitos previos

Python 3.11+ (o el que tengas disponible; probado con 3.12).

### 2. Entorno virtual e instalación de dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate      # en Windows: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Configurar credenciales de Alpaca

Copiá `.env.example` a `.env` y completá tus credenciales de la cuenta Paper Trading de Alpaca:

```bash
cp .env.example .env
```

```ini
ALPACA_API_KEY=TU_API_KEY_DE_PAPER_TRADING
ALPACA_SECRET_KEY=TU_SECRET_KEY_DE_PAPER_TRADING
ALPACA_BASE_URL=https://paper-api.alpaca.markets

SYMBOL=TSLA
BUY_AMOUNT=1000.0
MAX_BUYS=10
BUY_DROP_PCT=0.03
SELL_RISE_PCT=0.03
INTERVAL=1200
PAPER=true
```

Estas variables son los defaults de `tradebot.py`; cada una tiene su flag CLI equivalente que la sobreescribe (ver abajo).

---

## 🚀 Cómo Ejecutar Cada Script

### Bot en vivo / paper trading (`tradebot.py`)

```bash
python tradebot.py \
  --symbol TSLA \
  --buy-amount 10000 \
  --max-buys 10 \
  --buy-drop-pct 0.03 \
  --sell-rise-pct 0.03 \
  --interval 1200 \
  --paper
```

Cualquier flag omitido toma el valor de la variable de entorno correspondiente (`.env`). El bot reconcilia su estado local contra las posiciones reales en Alpaca al iniciar y en cada ciclo, guarda su pila de compras en `<symbol>_state.json`, y loguea en `tradebot_<symbol>.log`.

**Con Docker:**

```bash
cp .env.example .env   # completar credenciales
docker compose up -d
```

### Backtest histórico (`backtest.py`)

```bash
python backtest.py \
  --symbol TSLA \
  --date-start 2026-01-01 \
  --date-end 2026-06-28 \
  --buy-amount 10000 \
  --buy-drop-pct 0.05 \
  --sell-rise-pct 0.04 \
  --interval-minutes 20
```

Descarga (y cachea) velas de 1 minuto de Alpaca, simula la estrategia con esos parámetros y reporta ROI, ganancia y operaciones ejecutadas.

### Optimizador de parámetros (`optimize.py`)

```bash
python optimize.py \
  --symbol TSLA \
  --date-start 2026-01-01 \
  --date-end 2026-06-28 \
  --intervals 5,15,20,30,60
```

Prueba las 100 combinaciones de drop/rise (1–10% cada uno) por cada intervalo indicado, y genera `optimize_<symbol>_<timestamp>.log` (resumen legible) y `.csv` (resultados completos) con la mejor combinación global y por intervalo.

### Análisis walk-forward (`walk_forward.py`)

```bash
python walk_forward.py \
  --symbol TSLA \
  --date-start 2026-01-01 \
  --date-end 2026-07-03 \
  --intervals 20 \
  --period month \
  --train-periods 1
```

Parte el histórico en períodos (semanas o meses, según `--period week|month`, default `week`), mide cuánto varían los parámetros óptimos de un período a otro y compara en un torneo (con un solo portfolio continuo, sin liquidar posiciones al cambiar de parámetros) cuatro estrategias: parámetros fijos por mediana histórica, auto-ajuste periódico usando el pico del grid, una variante robusta por meseta, y un oráculo teórico con lookahead. Genera `walkforward_<symbol>_<timestamp>.log` (con un veredicto explícito sobre si el auto-ajuste se justifica) y `.csv` (historial de parámetros óptimos por período).

### Tests

```bash
python -m pytest test_walk_forward.py -v
```

Usa exclusivamente datos sintéticos — no requiere `.env` ni conexión a Alpaca.

---

## 📊 Hallazgos hasta ahora

Análisis walk-forward sobre TSLA (intervalo de 20 min), repetidos con muestras crecientes:

- **Granularidad semanal (`--period week`, default): el auto-ajuste NO se justifica.** Confirmado en tres corridas (27, 53 y 79 semanas): un parámetro fijo por mediana histórica (drop≈1%, rise≈3-4%) le gana consistentemente a re-optimizar cada semana con el pico del grid de la semana anterior. La autocorrelación del óptimo semanal ronda cero con muestras grandes — no hay nada que "seguir" de una semana a la otra.
- **Granularidad mensual (`--period month`): el resultado se invierte.** En tres corridas (12, 24 y 36 meses), la variante robusta de auto-ajuste (que promedia el vecindario del grid en vez de perseguir el pico exacto) le gana al fijo por un margen consistente de ~15-17pp en las dos muestras más grandes. Perseguir el pico exacto mensual sigue sin aportar mucho por sí solo — el valor está en la robustez, no en la precisión puntual.

Es decir, la cadencia de re-optimización parece importar tanto como si se auto-ajusta o no. Sigue siendo un solo símbolo (TSLA); falta validar en otros antes de confiar del todo en esto. Ver `docs/walk-forward-log.md` para el detalle completo de cada corrida (comandos exactos, artefactos, y las notas sobre por qué el "techo teórico" puede comportarse de forma contraintuitiva con pocas muestras).
