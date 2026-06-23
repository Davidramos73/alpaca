# Documentación del Backtest (`backtest.py`)

Este documento detalla el funcionamiento, la arquitectura y los resultados del script de simulación histórica [backtest.py](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/backtest.py).

---

## 📋 ¿Qué es `backtest.py`?

Es un script de simulación diseñado para evaluar de forma retroactiva el desempeño de la estrategia de trading definida en `tesla.py`. Utiliza datos históricos de mercado reales de **Tesla (TSLA)** del período comprendido entre el **1 de Enero de 2026 y el 22 de Junio de 2026**.

Al simular la estrategia en el pasado, permite comprender los riesgos, la cantidad de operaciones que genera la cuadrícula y el retorno esperado antes de poner en riesgo capital demo o real.

---

## ⚙️ Parámetros de la Simulación

* **Capital Inicial**: $100,000 USD (ficticios).
* **Monto por Operación**: $10,000 USD (la cuenta se divide en 10 partes).
* **Frecuencia de Datos**: Barras históricas de **1 Hora** (`TimeFrame.Hour`) descargadas desde la API de Alpaca.
* **Límite de Compras**: Máximo 10 compras acumuladas en la cuadrícula.
* **Margen de Caída (Compra)**: **-5%** respecto al último precio de compra.
* **Margen de Subida (Venta)**: **+4%** respecto al último precio de compra (Pila LIFO).

---

## 🧠 Lógica del Algoritmo de Simulación

El script recorre secuencialmente cada una de las 1,857 horas de trading del período:

1. **Compra Inicial**: Si la pila de compras está vacía, compra inmediatamente $10,000 de TSLA al precio de cierre de esa hora.
2. **Evaluación de Cuadrícula (LIFO)**:
   - Toma el precio del último lote en la pila (`purchases[-1]['price']`).
   - Si el precio de cierre de la hora actual es **menor o igual al 95%** de ese precio:
     - Realiza una nueva compra de $10,000 y la añade a la pila.
   - Si el precio de cierre de la hora actual es **mayor o igual al 104%** de ese precio:
     - Vende la cantidad exacta de acciones de ese lote.
     - Remueve el lote de la pila. El precio de referencia vuelve a ser el del lote anterior.
3. **Persistencia y Registro**:
   - Cada operación calcula el balance actual (efectivo libre + valor de mercado de las acciones restantes).
   - Se registra cada transacción en tiempo real en el archivo local [backtest.log](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/backtest.log).

---

## 📈 Resultados del Período Analizado (Ene 2026 - Jun 2026)

| Métrica | Valor |
| --- | --- |
| **Capital Inicial** | $100,000.00 |
| **Capital Final Total** | $106,016.68 |
| **Efectivo Final Disponible** | $77,802.64 |
| **Valor de Acciones Retenidas** | $28,214.04 (`70.800609` acciones) |
| **Ganancia Neta** | **+$6,016.68** |
| **Retorno de la Inversión (ROI)**| **+6.02%** |
| **Precio Inicial TSLA** | $449.58 |
| **Precio Final TSLA** | $398.50 (**Caída del -11.36%**) |
| **Total de Compras Ejecutadas** | 19 |
| **Total de Ventas Ejecutadas** | 16 |
| **Lotes Activos al Finalizar** | 3 / 10 |

### Conclusión Clave:
La acción de Tesla cayó un **-11.36%** en este período, lo que habría significado pérdidas para un inversor pasivo de tipo *Buy and Hold*. Sin embargo, la estrategia de Grid Trading obtuvo un **+6.02% de ganancia** debido a la captura constante de micro-oscilaciones intradía.

---

## 📝 Formato del Registro (`backtest.log`)

El archivo [backtest.log](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/backtest.log) detalla la cronología de la simulación. Ejemplo de registros:

* **Compra inicial / Grid**:
  ```text
  [2026-01-20 09:00] COMPRA GRID: 23.612193 acciones a $423.51. Efectivo: $80,000.00 | Balance Total: $99,420.13 | Compras activas: 2/10
  ```
* **Venta de un lote específico con ganancia**:
  ```text
  [2026-01-22 17:00] VENTA LOTE: 23.612193 acciones a $445.51 (Compra original a $423.51). Ganancia lote: $+519.47 | Efectivo: $90,519.47 | Balance Total: $100,428.94 | Compras activas restantes: 1/10
  ```
