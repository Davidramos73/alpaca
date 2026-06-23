# Bot de Grid Trading TSLA con Alpaca

Este proyecto implementa un robot de trading algorítmico escrito en Python que ejecuta una estrategia de **Grid Trading (Cuadrícula LIFO)** para operar con acciones de **Tesla (TSLA)** de forma automática utilizando la API de **Alpaca** (Paper Trading / Cuenta Demo).

---

## 📈 Lógica de la Estrategia

El bot opera bajo las siguientes reglas definidas:

1. **Partición de Capital**: Divide la cuenta demo de $100,000 en 10 bloques de **$10,000** cada uno. El bot puede acumular un máximo de 10 compras en simultáneo.
2. **Inicio del Grid**: Al arrancar por primera vez sin estado previo, realiza una compra a mercado inicial de **$10,000** de TSLA para fijar el primer precio de referencia.
3. **Pila de Memoria (LIFO)**: Las compras se guardan en orden secuencial (como una pila). La última compra realizada siempre define los límites actuales del bot.
4. **Compra por Caída (-5%)**: Si el precio actual de TSLA cae un **5% o más** respecto al precio de la última compra de la pila (y hay menos de 10 compras activas), el bot realiza una nueva compra de $10,000 a mercado y la apila.
5. **Venta por Subida (+4%)**: Si el precio actual sube un **4% o más** respecto al precio de la última compra de la pila, el bot vende únicamente la cantidad exacta de acciones de ese último lote, tomándolo de la pila (Pop). 
   - El precio de referencia en memoria regresa automáticamente al precio de la compra anterior de la pila.
   - Si se venden todas las posiciones, el ciclo se reinicia en la siguiente iteración con una nueva compra de $10,000.

---

## 📁 Estructura de Archivos

* **[tesla.py](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/tesla.py)**: Script principal en Python con el bucle de ejecución y llamadas a Alpaca.
* **[backtest.py](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/backtest.py)**: Script de simulación histórica que evalúa la estrategia sobre datos reales del mercado (1 de Enero de 2026 - 22 de Junio de 2026).
* **[tesla_state.json](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/tesla_state.json)**: Archivo JSON local que almacena la pila de compras activa para asegurar persistencia si el script se interrumpe.
* **[tesla_bot.log](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/tesla_bot.log)**: Registro histórico detallado de todas las lecturas de precio, ejecuciones de órdenes y estado de cuenta.
* **[.env](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/.env)**: Archivo de configuración local para las claves de API privadas de Alpaca.
* **[requirements.txt](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/requirements.txt)**: Lista de dependencias del proyecto.

---

## 🛠️ Configuración e Instalación

### 1. Requisitos Previos
Asegúrate de tener instalado Python 3.8+ en tu sistema.

### 2. Configurar Entorno Virtual e Instalar Dependencias
Instala los paquetes necesarios dentro del entorno virtual del proyecto:

```bash
# Activar entorno virtual (en Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Instalar dependencias
pip install -r requirements.txt
```

### 3. Configurar Credenciales de Alpaca
Crea o edita tu archivo [.env](file:///c:/Users/Carlos%20Alcal%C3%A1/Documents/antigravity/ALPACA/.env) en la raíz del proyecto agregando tus credenciales de la cuenta Demo de Alpaca:

```ini
ALPACA_API_KEY=TU_API_KEY_DE_PAPER_TRADING
ALPACA_SECRET_KEY=TU_SECRET_KEY_DE_PAPER_TRADING
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

---

## 🚀 Cómo Ejecutar el Bot y el Backtest

### Ejecución del Bot en Tiempo Real (Demo)
Una vez configuradas las credenciales, inicia el bot con el siguiente comando:

```bash
python tesla.py
```

El script imprimirá en pantalla las consultas del precio cada 20 minutos, así como los detalles de las compras o ventas ejecutadas en tu cuenta de simulación.

### Ejecución de la Simulación Histórica (Backtest)
Para probar la estrategia con datos reales históricos (1 de Enero de 2026 al 22 de Junio de 2026), ejecuta:

```bash
python backtest.py
```

Esto descargará las barras de precios de TSLA en intervalos de 1 hora directamente desde Alpaca y generará un reporte detallado con las transacciones simuladas, la ganancia neta, el ROI y las posiciones finales.
