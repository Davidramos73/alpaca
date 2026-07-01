import os
import argparse
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

def main():
    parser = argparse.ArgumentParser(description="Backtest de estrategia grid")
    parser.add_argument("--symbol",     type=str,   default="TSLA",       help="Símbolo a analizar (default: TSLA)")
    parser.add_argument("--date-start", type=str,   default="2026-01-01", help="Fecha inicio YYYY-MM-DD (default: 2026-01-01)")
    parser.add_argument("--date-end",   type=str,   default="2026-06-28", help="Fecha fin YYYY-MM-DD (default: 2026-06-28)")
    parser.add_argument("--buy-amount", type=float, default=10000.0,      help="Monto base por compra en USD (default: 10000)")
    parser.add_argument("--max-buys", type=int, default=10, help="Máximo de compras activas simultáneas (default: 10)")
    parser.add_argument("--buy-drop-pct", type=float, default=0.05, help="Porcentaje de caída para nueva compra (default: 0.05 = 5%%)")
    parser.add_argument("--sell-rise-pct", type=float, default=0.04, help="Porcentaje de subida para venta (default: 0.04 = 4%%)")
    parser.add_argument("--fee-pct", type=float, default=0.0, help="Fee por operación sobre el monto (default: 0.0). Ej: 0.001 = 0.1%%")
    parser.add_argument("--no-profit-pool", action="store_true", help="Desactivar reinversión de ganancias (modo clásico)")
    args = parser.parse_args()

    # Cargar variables de entorno
    load_dotenv()
    
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        print("Error: No se encontraron las credenciales en el archivo .env")
        return
        
    # 1. Obtener datos históricos (con caché local por símbolo y período)
    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    cache_path = f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}.pkl"

    print(f"Iniciando simulación (Backtest) de {symbol}…")
    print(f"Rango: {date_start.strftime('%Y-%m-%d')} al {date_end.strftime('%Y-%m-%d')}")

    if os.path.exists(cache_path):
        print(f"Cargando datos desde caché ({cache_path})…")
        df = pd.read_pickle(cache_path)
    else:
        print(f"Descargando datos históricos de Alpaca para {symbol}…")
        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Hour,
            start=date_start,
            end=date_end,
        )
        try:
            bars = client.get_stock_bars(req)
            df = bars.df.reset_index()
            df.to_pickle(cache_path)
            print(f"Datos guardados en caché ({cache_path})")
        except Exception as e:
            print(f"Error al obtener datos históricos: {e}")
            return

    print(f"Datos listos. Total de horas de trading analizadas: {len(df)}")
    
    # 2. Configuración de parámetros de la estrategia
    starting_cash = 100000.0
    cash = starting_cash
    purchases = []  # Pila LIFO de compras: [{"price": float, "qty": float}]
    profit_pool   = 0.0

    buy_amount    = args.buy_amount
    max_buys      = args.max_buys
    buy_drop_pct  = args.buy_drop_pct
    sell_rise_pct = args.sell_rise_pct
    fee_pct       = args.fee_pct
    use_pool      = not args.no_profit_pool

    # Estadísticas del backtest
    total_buys_count  = 0
    total_sells_count = 0
    total_fees        = 0.0
    trades_log        = []
    run_ts            = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_file_path = f"backtest_{symbol}_{run_ts}.log"
    with open(log_file_path, "w", encoding="utf-8") as lf:
        lf.write(f"=== INICIO DE SIMULACIÓN HISTÓRICA {symbol} ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n")
        lf.write(f"Capital Inicial: ${starting_cash:,.2f} | Buy Amount: ${buy_amount:,.2f} | Fee: {fee_pct*100:.3f}% | Pool de ganancias: {'ON' if use_pool else 'OFF'}\n")
        lf.write("=================================================================================\n\n")
    
    # 3. Ejecución de la simulación barra a barra
    for _, row in df.iterrows():
        timestamp = row['timestamp']
        close_price = float(row['close'])
        
        # Caso A: No hay compras activas. Ejecutamos la compra inicial.
        if len(purchases) == 0:
            free_slots   = max_buys - len(purchases)
            bonus        = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
            effective_buy = buy_amount + bonus
            qty      = effective_buy / close_price
            buy_fee  = effective_buy * fee_pct
            cash    -= effective_buy + buy_fee
            if use_pool:
                profit_pool -= bonus
            total_fees += buy_fee
            purchase_info = {"price": close_price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy, "timestamp": timestamp}
            purchases.append(purchase_info)
            total_buys_count += 1

            total_equity = cash + (qty * close_price)

            trades_log.append({
                "type": "BUY_INIT",
                "price": close_price,
                "qty": qty,
                "fee": buy_fee,
                "cash_remaining": cash,
                "timestamp": timestamp
            })

            with open(log_file_path, "a", encoding="utf-8") as lf:
                lf.write(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] COMPRA INICIAL: {qty:.6f} acciones a ${close_price:.2f} (${effective_buy:.2f}) | Fee: ${buy_fee:.2f} | Pool: ${profit_pool:.2f} | Efectivo: ${cash:,.2f} | Balance Total: ${total_equity:,.2f}\n")
            continue
            
        # Obtener última compra de la pila
        last_purchase = purchases[-1]
        last_buy_price = last_purchase["price"]
        
        buy_target = last_buy_price * (1.0 - buy_drop_pct)
        sell_target = last_buy_price * (1.0 + sell_rise_pct)
        
        # Caso B: El precio cruzó el objetivo de compra (-5%)
        if close_price <= buy_target:
            if len(purchases) < max_buys:
                free_slots    = max_buys - len(purchases)
                bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
                effective_buy = buy_amount + bonus
                qty      = effective_buy / close_price
                buy_fee  = effective_buy * fee_pct
                cash    -= effective_buy + buy_fee
                if use_pool:
                    profit_pool -= bonus
                total_fees += buy_fee
                purchase_info = {"price": close_price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy, "timestamp": timestamp}
                purchases.append(purchase_info)
                total_buys_count += 1

                holdings_val = sum(p['qty'] for p in purchases) * close_price
                total_equity = cash + holdings_val

                trades_log.append({
                    "type": "BUY_GRID",
                    "price": close_price,
                    "qty": qty,
                    "fee": buy_fee,
                    "cash_remaining": cash,
                    "timestamp": timestamp
                })

                with open(log_file_path, "a", encoding="utf-8") as lf:
                    lf.write(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] COMPRA GRID: {qty:.6f} acciones a ${close_price:.2f} (${effective_buy:.2f}) | Fee: ${buy_fee:.2f} | Pool: ${profit_pool:.2f} | Efectivo: ${cash:,.2f} | Balance Total: ${total_equity:,.2f} | Compras activas: {len(purchases)}/{max_buys}\n")
                
        # Caso C: El precio cruzó el objetivo de venta (+4%)
        elif close_price >= sell_target:
            removed_purchase = purchases.pop()
            qty_to_sell  = removed_purchase["qty"]
            revenue      = qty_to_sell * close_price
            sell_fee     = revenue * fee_pct
            cash        += revenue - sell_fee
            total_fees  += sell_fee
            total_sells_count += 1

            cost_basis   = removed_purchase["effective_buy"] + removed_purchase["buy_fee"]
            profit       = (revenue - sell_fee) - cost_basis
            if use_pool and profit > 0:
                profit_pool += profit

            holdings_val = sum(p['qty'] for p in purchases) * close_price
            total_equity = cash + holdings_val

            trades_log.append({
                "type": "SELL",
                "buy_price": removed_purchase["price"],
                "sell_price": close_price,
                "qty": qty_to_sell,
                "fee": sell_fee,
                "profit": profit,
                "cash_remaining": cash,
                "timestamp": timestamp
            })

            with open(log_file_path, "a", encoding="utf-8") as lf:
                lf.write(f"[{timestamp.strftime('%Y-%m-%d %H:%M')}] VENTA LOTE: {qty_to_sell:.6f} acciones a ${close_price:.2f} (Compra: ${removed_purchase['price']:.2f}) | Fee: ${sell_fee:.2f} | Ganancia neta: ${profit:+,.2f} | Pool: ${profit_pool:.2f} | Efectivo: ${cash:,.2f} | Balance Total: ${total_equity:,.2f} | Activas: {len(purchases)}/{max_buys}\n")
            
    # 4. Resultados finales
    final_price = float(df.iloc[-1]['close'])
    remaining_shares = sum(p['qty'] for p in purchases)
    holdings_value = remaining_shares * final_price
    total_equity = cash + holdings_value
    total_profit = total_equity - starting_cash
    roi = (total_profit / starting_cash) * 100
    
    # Escribir resumen final al archivo .log
    with open(log_file_path, "a", encoding="utf-8") as lf:
        lf.write("\n=================================================================================\n")
        lf.write("                             RESULTADOS FINALES                                  \n")
        lf.write("=================================================================================\n")
        lf.write(f"Período:             {df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} a {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}\n")
        lf.write(f"Precio Inicial {symbol}: ${df.iloc[0]['close']:.2f}\n")
        lf.write(f"Precio Final {symbol}:   ${final_price:.2f}\n")
        lf.write(f"Capital Inicial:     ${starting_cash:,.2f}\n")
        lf.write(f"Efectivo Final:      ${cash:,.2f}\n")
        lf.write(f"Valor de Acciones:   ${holdings_value:,.2f} ({remaining_shares:.6f} acciones)\n")
        lf.write(f"Capital Final Total: ${total_equity:,.2f}\n")
        lf.write(f"Ganancia/Pérdida:    ${total_profit:+,.2f}\n")
        lf.write(f"Retorno (ROI):       {roi:+.2f}%\n")
        lf.write(f"Fee por operación:   {fee_pct*100:.3f}%\n")
        lf.write(f"Total Fees Pagados:  ${total_fees:,.2f}\n")
        lf.write(f"Total de Compras:    {total_buys_count}\n")
        lf.write(f"Total de Ventas:     {total_sells_count}\n")
        lf.write(f"Compras Activas:     {len(purchases)}/{max_buys}\n")
        lf.write(f"Pool de ganancias:   ${profit_pool:,.2f} ({'ON' if use_pool else 'OFF'})\n")
        lf.write("=================================================================================\n")

    print("\n==================================================")
    print("             RESULTADOS DEL BACKTEST              ")
    print("==================================================")
    print(f"Período:             {df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} a {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}")
    print(f"Precio Inicial {symbol}: ${df.iloc[0]['close']:.2f}")
    print(f"Precio Final {symbol}:   ${final_price:.2f}")
    print("--------------------------------------------------")
    print(f"Capital Inicial:     ${starting_cash:,.2f}")
    print(f"Efectivo Final:      ${cash:,.2f}")
    print(f"Valor de Acciones:   ${holdings_value:,.2f} ({remaining_shares:.6f} acciones)")
    print(f"Capital Final Total: ${total_equity:,.2f}")
    print("--------------------------------------------------")
    print(f"Ganancia/Pérdida:    ${total_profit:+,.2f}")
    print(f"Retorno (ROI):       {roi:+.2f}%")
    print("--------------------------------------------------")
    print(f"Fee por operación:   {fee_pct*100:.3f}%")
    print(f"Total Fees Pagados:  ${total_fees:,.2f}")
    print("--------------------------------------------------")
    print(f"Total de Compras:    {total_buys_count}")
    print(f"Total de Ventas:     {total_sells_count}")
    print(f"Compras Activas:     {len(purchases)}/{max_buys}")
    print(f"Pool de ganancias:   ${profit_pool:,.2f} ({'ON' if use_pool else 'OFF'})")
    print("==================================================")
    print(f"Detalle completo guardado en: {log_file_path}")
    
    print("\nÚltimas 10 transacciones registradas:")
    for t in trades_log[-10:]:
        date_str = t['timestamp'].strftime('%Y-%m-%d %H:%M')
        if "BUY" in t['type']:
            print(f"[{date_str}] COMPRA - Precio: ${t['price']:.2f} | Acciones: {t['qty']:.4f} | Efectivo: ${t['cash_remaining']:,.2f}")
        else:
            print(f"[{date_str}] VENTA  - Compra: ${t['buy_price']:.2f} -> Venta: ${t['sell_price']:.2f} | Ganancia: ${t['profit']:+,.2f} | Efectivo: ${t['cash_remaining']:,.2f}")

if __name__ == '__main__':
    main()
