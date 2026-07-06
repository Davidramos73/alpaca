import os
import argparse
from datetime import datetime
from dotenv import load_dotenv

from optimize import STARTING_CASH, buy_hold_roi, load_bars, simulate


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
    parser.add_argument("--interval-minutes", type=int, default=20, help="Cada cuántos minutos 'revisa' el precio el bot simulado, igual que el parámetro --interval del bot real (default: 20)")
    parser.add_argument("--cooldown-minutes", type=float, default=0.0, help="Minutos de mercado sin comprar tras cada compra de grid (default: 0 = apagado)")
    parser.add_argument("--reserved-slots",   type=int,   default=0,   help="Slots finales reservados para caídas profundas (default: 0 = apagado)")
    parser.add_argument("--deep-drop-pct",    type=float, default=0.0, help="Caída mínima desde el pivot para usar los slots reservados. Ej: 0.25 = 25%%")
    parser.add_argument("--breaker-dd-pct",   type=float, default=0.0, help="Umbral de drawdown del equity que congela compras (default: 0 = apagado). Ej: 0.15 = 15%%")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: No se encontraron las credenciales en el archivo .env")
        return

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")

    print(f"Iniciando simulación (Backtest) de {symbol}…")
    print(f"Rango: {date_start.strftime('%Y-%m-%d')} al {date_end.strftime('%Y-%m-%d')}")

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    print(f"Datos descargados: {len(df_1min)} velas de 1 minuto.")

    interval_minutes = max(1, args.interval_minutes)
    df = df_1min.iloc[::interval_minutes].reset_index(drop=True)
    print(f"Simulando con intervalo de revisión de {interval_minutes} minuto(s): {len(df)} precios evaluados.")

    buy_amount = args.buy_amount
    fee_pct    = args.fee_pct
    use_pool   = not args.no_profit_pool

    run_ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = f"backtest_{symbol}_{run_ts}.log"
    trades_log    = []

    with open(log_file_path, "w", encoding="utf-8") as lf:
        lf.write(f"=== INICIO DE SIMULACIÓN HISTÓRICA {symbol} ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n")
        lf.write(f"Capital Inicial: ${STARTING_CASH:,.2f} | Buy Amount: ${buy_amount:,.2f} | Fee: {fee_pct*100:.3f}% | Pool de ganancias: {'ON' if use_pool else 'OFF'}\n")
        lf.write("=================================================================================\n\n")

        def on_trade(ev):
            trades_log.append(ev)
            t = ev["timestamp"].strftime("%Y-%m-%d %H:%M")
            if ev["type"] in ("BUY_INIT", "BUY_GRID"):
                etiqueta = "COMPRA INICIAL" if ev["type"] == "BUY_INIT" else "COMPRA GRID"
                lf.write(f"[{t}] {etiqueta}: {ev['qty']:.6f} acciones a ${ev['price']:.2f} | Fee: ${ev['fee']:.2f} | Pool: ${ev['pool']:.2f} | Efectivo: ${ev['cash']:,.2f} | Activas: {ev['open_positions']}/{args.max_buys}\n")
            else:
                lf.write(f"[{t}] VENTA LOTE: {ev['qty']:.6f} acciones a ${ev['price']:.2f} (Compra: ${ev['buy_price']:.2f}) | Fee: ${ev['fee']:.2f} | Ganancia neta: ${ev['profit']:+,.2f} | Pool: ${ev['pool']:.2f} | Efectivo: ${ev['cash']:,.2f} | Activas: {ev['open_positions']}/{args.max_buys}\n")

        r = simulate(df, args.max_buys, args.buy_drop_pct, args.sell_rise_pct, fee_pct,
                     use_pool=use_pool, buy_amount=buy_amount,
                     interval_minutes=interval_minutes, on_trade=on_trade,
                     cooldown_minutes=args.cooldown_minutes,
                     reserved_slots=args.reserved_slots,
                     deep_drop_pct=args.deep_drop_pct,
                     breaker_dd_pct=args.breaker_dd_pct)

        bh               = buy_hold_roi(df_1min)
        final_price      = float(df.iloc[-1]["close"])
        remaining_shares = sum(p["qty"] for p in r["state"]["purchases"])
        holdings_value   = remaining_shares * final_price
        cash             = r["state"]["cash"]

        resumen = [
            "",
            "=================================================================================",
            "                             RESULTADOS FINALES                                  ",
            "=================================================================================",
            f"Período:             {df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} a {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}",
            f"Precio Inicial {symbol}: ${df.iloc[0]['close']:.2f}",
            f"Precio Final {symbol}:   ${final_price:.2f}",
            f"Capital Inicial:     ${STARTING_CASH:,.2f}",
            f"Efectivo Final:      ${cash:,.2f}",
            f"Valor de Acciones:   ${holdings_value:,.2f} ({remaining_shares:.6f} acciones)",
            f"Capital Final Total: ${r['total_equity']:,.2f}",
            f"Ganancia/Pérdida:    ${r['profit']:+,.2f}",
            f"Retorno (ROI):       {r['roi']:+.2f}%",
            f"Drawdown máximo:     {r['max_drawdown_pct']:.2f}%",
            f"Buy & Hold ref.:     {bh['roi']:+.2f}%  (maxDD {bh['max_drawdown_pct']:.2f}%)",
            f"Fee por operación:   {fee_pct*100:.3f}%",
            f"Total Fees Pagados:  ${r['total_fees']:,.2f}",
            f"Total de Compras:    {r['buys']}",
            f"Total de Ventas:     {r['sells']}",
            f"Compras Activas:     {r['open_positions']}/{args.max_buys}",
            f"Pool de ganancias:   ${r['state']['profit_pool']:,.2f} ({'ON' if use_pool else 'OFF'})",
            "=================================================================================",
        ]
        lf.write("\n".join(resumen) + "\n")

    print("\n".join(resumen))
    print(f"Detalle completo guardado en: {log_file_path}")

    print("\nÚltimas 10 transacciones registradas:")
    for t in trades_log[-10:]:
        date_str = t["timestamp"].strftime("%Y-%m-%d %H:%M")
        if t["type"] in ("BUY_INIT", "BUY_GRID"):
            print(f"[{date_str}] COMPRA - Precio: ${t['price']:.2f} | Acciones: {t['qty']:.4f} | Efectivo: ${t['cash']:,.2f}")
        else:
            print(f"[{date_str}] VENTA  - Compra: ${t['buy_price']:.2f} -> Venta: ${t['price']:.2f} | Ganancia: ${t['profit']:+,.2f} | Efectivo: ${t['cash']:,.2f}")


if __name__ == "__main__":
    main()
