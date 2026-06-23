import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Cargar variables de entorno desde el archivo .env
load_dotenv()

api_key = os.getenv("ALPACA_API_KEY")
secret_key = os.getenv("ALPACA_SECRET_KEY")

if not api_key or not secret_key:
    print("Error: No se encontraron las credenciales ALPACA_API_KEY o ALPACA_SECRET_KEY en el archivo .env")
    exit(1)

# Inicializar el cliente de datos históricos para acciones
client = StockHistoricalDataClient(api_key, secret_key)

print("Obteniendo precios de Tesla (TSLA)...")

# Configurar la solicitud para obtener los datos de los últimos 5 días
request_params = StockBarsRequest(
    symbol_or_symbols="TSLA",
    timeframe=TimeFrame.Day,
    start=datetime.now() - timedelta(days=5)
)

try:
    # Obtener barras de datos históricos de acciones
    bars = client.get_stock_bars(request_params)
    
    # Convertir a DataFrame e imprimir
    df = bars.df
    print("\n--- Precios Históricos Recientes de TSLA ---")
    print(df)
except Exception as e:
    print(f"Error al obtener los datos de Alpaca: {e}")
