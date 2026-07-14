import os
import json
import time
import math
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest


def setup_logging(symbol: str):
    log_file = f"tradebot_{symbol}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return log_file


def load_state(state_file: str) -> dict:
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                if "purchases" not in state:
                    state["purchases"] = []
                if "profit_pool" not in state:
                    state["profit_pool"] = 0.0
                return state
        except Exception:
            logging.exception(f"Error al leer {state_file}. Se iniciará estado vacío.")
    return {"purchases": [], "profit_pool": 0.0}


def save_state(state: dict, state_file: str):
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4, ensure_ascii=False)
    except Exception:
        logging.exception(f"Error al guardar estado en {state_file}")


def fetch_symbol_fills(trading_client, symbol: str) -> list:
    """Trae TODOS los fills del símbolo desde el historial de actividades de
    Alpaca (endpoint /v2/account/activities/FILL, paginado — alpaca-py no lo
    expone en TradingClient, así que se usa el .get() REST genérico) y los
    normaliza a dicts ordenados cronológicamente."""
    raw = []
    page_token = None
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        page = trading_client.get("/account/activities/FILL", params)
        if not page:
            break
        raw.extend(a for a in page if a.get("symbol") == symbol)
        page_token = str(page[-1]["id"])
        if len(page) < 100:
            break
    fills = []
    for a in raw:
        side = str(a.get("side", "")).lower()
        fills.append({
            "side": "buy" if "buy" in side else "sell",
            "qty": abs(float(a["qty"])),
            "price": float(a["price"]),
            "order_id": str(a.get("order_id") or ""),
            "timestamp": str(a.get("transaction_time", "")),
        })
    fills.sort(key=lambda f: f["timestamp"])
    return fills


def replay_fills_lifo(fills: list, broker_qty: float, tol: float = 1e-4) -> list | None:
    """Reproduce los fills en orden cronológico para reconstruir los lotes
    abiertos con sus precios REALES de compra. Los fills parciales de una
    misma orden de compra se fusionan en un lote (precio promedio ponderado);
    las ventas descuentan LIFO — la misma convención con la que el bot vende
    (siempre el último lote) — así los lotes que quedan coinciden con los que
    el grid habría dejado abiertos. Devuelve None si el resultado no cuadra
    con la posición real (historial incompleto o trades manuales sobre el
    mismo símbolo): en ese caso el caller debe usar el fallback por promedio."""
    lots: list = []
    for f in fills:
        if f["side"] == "buy":
            if lots and f["order_id"] and lots[-1]["order_id"] == f["order_id"]:
                last = lots[-1]
                total = last["qty"] + f["qty"]
                last["price"] = (last["price"] * last["qty"] + f["price"] * f["qty"]) / total
                last["qty"] = total
            else:
                lots.append({
                    "price": f["price"], "qty": f["qty"],
                    "order_id": f["order_id"] or "reconciled",
                    "timestamp": f["timestamp"],
                })
        else:
            rem = f["qty"]
            while rem > tol and lots:
                take = min(rem, lots[-1]["qty"])
                lots[-1]["qty"] -= take
                rem -= take
                if lots[-1]["qty"] <= tol:
                    lots.pop()
            if rem > tol:
                return None
    if abs(sum(l["qty"] for l in lots) - broker_qty) > tol:
        return None
    for l in lots:
        l["price"] = round(l["price"], 4)
    return lots


def reconcile_with_broker(
    trading_client, symbol: str, local_state: dict, buy_amount: float, max_buys: int
) -> dict | None:
    """Verifica el estado local contra Alpaca. Si hay órdenes abiertas sin
    resolver, no continúa (es ambiguo qué acción tomar). Si la posición real
    no coincide con el estado local, reconstruye purchases desde el historial
    de fills de Alpaca (replay LIFO -> lotes con precios reales de compra).
    Si el historial no cuadra con la posición (trades manuales sobre el mismo
    símbolo, historial truncado), cae al fallback anterior: repartir la
    posición en lotes de tamaño ~buy_amount, todos al precio promedio de
    `avg_entry_price`. En ambos casos profit_pool se resetea a 0 (es un
    concepto interno del bot, no recuperable desde Alpaca)."""
    try:
        open_orders = trading_client.get_orders(
            filter=GetOrdersRequest(symbols=[symbol], status=QueryOrderStatus.OPEN)
        )
    except Exception:
        logging.exception(f"No se pudo verificar órdenes abiertas de {symbol}")
        return None

    if open_orders:
        ids = ", ".join(str(o.id) for o in open_orders)
        logging.critical(
            f"Hay {len(open_orders)} orden(es) abierta(s) sin resolver para "
            f"{symbol} (IDs: {ids}). El bot NO se iniciará para evitar duplicar "
            "órdenes. Resuélvelas manualmente en Alpaca antes de reintentar."
        )
        return None

    try:
        position = trading_client.get_open_position(symbol)
        broker_qty = abs(float(position.qty))
        avg_price = float(position.avg_entry_price)
    except APIError:
        broker_qty = 0.0
        avg_price = 0.0

    local_purchases = local_state.get("purchases", [])
    local_qty = sum(p["qty"] for p in local_purchases)

    if abs(local_qty - broker_qty) <= 1e-4:
        return local_state

    logging.warning(
        f"DISCREPANCIA de posición para {symbol}: estado local {local_qty:.6f} "
        f"acciones vs Alpaca {broker_qty:.6f} (precio promedio ${avg_price:.2f}). "
        "Reconstruyendo estado desde la posición real (se resetea profit_pool)."
    )

    purchases: list | None = None
    if broker_qty <= 0:
        purchases = []
    else:
        try:
            fills = fetch_symbol_fills(trading_client, symbol)
            purchases = replay_fills_lifo(fills, broker_qty)
        except Exception:
            logging.exception(
                f"No se pudo leer el historial de fills de {symbol}; "
                "se reconstruirá por precio promedio."
            )
        if purchases is not None:
            logging.warning(
                f"Estado reconstruido desde el historial de fills de Alpaca: "
                f"{len(purchases)} lote(s) con precios reales de compra, "
                f"profit_pool=$0.00. Reemplazando estado local previo "
                f"({len(local_purchases)} lote(s))."
            )
        else:
            logging.warning(
                f"El historial de fills de {symbol} no cuadra con la posición "
                "(trades manuales o historial incompleto). Fallback: lotes al "
                "precio promedio."
            )

    if purchases is None:
        position_value = broker_qty * avg_price
        estimated_lots = max(1, round(position_value / buy_amount))
        estimated_lots = min(estimated_lots, max_buys)
        lot_qty = broker_qty / estimated_lots
        now_iso = datetime.utcnow().isoformat()
        purchases = [
            {"price": avg_price, "qty": lot_qty, "order_id": "reconciled", "timestamp": now_iso}
            for _ in range(estimated_lots)
        ]
        logging.warning(
            f"Estado reconstruido desde Alpaca: {len(purchases)} lote(s) estimados "
            f"a ${avg_price:.2f} c/u, profit_pool=$0.00. Reemplazando estado local "
            f"previo ({len(local_purchases)} lote(s))."
        )
    for i, p in enumerate(purchases):
        logging.info(f"  [{i+1}] ${p['price']:.2f} | {p['qty']:.6f} acc | {p['timestamp']}")

    return {"purchases": purchases, "profit_pool": 0.0}


def floor2(x: float) -> float:
    """Trunca a 2 decimales sin redondear hacia arriba, para nunca pedir
    más notional del disponible (Alpaca exige máximo 2 decimales)."""
    return math.floor(x * 100 + 1e-6) / 100


def get_latest_price(data_client, symbol: str):
    try:
        req = StockLatestTradeRequest(symbol_or_symbols=symbol)
        latest_trade = data_client.get_stock_latest_trade(req)
        return float(latest_trade[symbol].price)
    except Exception:
        logging.exception(f"Error al obtener precio de {symbol}")
        return None


def wait_for_order_fill(trading_client, order_id, max_attempts=15, delay=1):
    for attempt in range(max_attempts):
        try:
            order = trading_client.get_order_by_id(order_id)
            if order.status == OrderStatus.FILLED:
                return order
            if order.status in [OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED]:
                raise Exception(f"La orden fue {order.status.value}")
        except Exception as e:
            if "La orden fue" in str(e):
                raise
            logging.warning(f"Intento {attempt + 1}: Error al consultar orden {order_id}: {e}")
        time.sleep(delay)
    raise TimeoutError(f"La orden {order_id} no se completó en el tiempo esperado.")


def resolve_pending_order(trading_client, order_id):
    """Se llama cuando wait_for_order_fill se rindió sin confirmar. Nunca
    asume que la orden falló: consulta su estado real en Alpaca. Si en
    realidad ya se llenó (solo tardó más de lo esperado), la toma como
    exitosa. Si sigue viva, la cancela explícitamente y vuelve a
    consultarla — cancelar puede perder la carrera contra un fill que llega
    justo en ese instante, por eso el segundo chequeo es la fuente de
    verdad. Nunca deja la orden flotando para que el caller reintente a
    ciegas (eso fue lo que causó una triple venta del mismo lote en
    producción: dos órdenes 'timeouteadas' terminaron llenándose igual)."""
    try:
        order = trading_client.get_order_by_id(order_id)
    except Exception:
        logging.exception(f"No se pudo consultar la orden {order_id} tras el timeout")
        return None

    if order.status == OrderStatus.FILLED:
        return order
    if order.status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
        filled_qty = float(order.filled_qty or 0)
        if filled_qty > 0:
            logging.warning(f"Orden {order_id} terminó {order.status.value} con fill parcial de {filled_qty:.6f}.")
            return order
        return None

    logging.warning(f"Orden {order_id} sigue '{order.status.value}' tras el timeout. Cancelando antes de reintentar...")
    try:
        trading_client.cancel_order_by_id(order_id)
    except Exception:
        logging.exception(f"No se pudo cancelar la orden {order_id}")

    try:
        order = trading_client.get_order_by_id(order_id)
    except Exception:
        logging.exception(f"No se pudo re-consultar la orden {order_id} tras cancelarla")
        return None

    filled_qty = float(order.filled_qty or 0)
    if order.status == OrderStatus.FILLED or filled_qty > 0:
        if order.status != OrderStatus.FILLED:
            logging.warning(f"Orden {order_id} quedó con fill parcial ({filled_qty:.6f}) pese a la cancelación.")
        return order

    logging.info(f"Orden {order_id} cancelada limpia, sin ejecución. Segura para reintentar.")
    return None


def execute_buy(trading_client, symbol: str, amount: float) -> dict | None:
    try:
        account = trading_client.get_account()
        available_cash = float(account.cash)
        if available_cash < amount:
            logging.error(f"Capital insuficiente. Disponible: ${available_cash:,.2f} | Requerido: ${amount:,.2f}")
            return None
        logging.info(f"Capital disponible: ${available_cash:,.2f}")
    except Exception:
        logging.exception("No se pudo verificar el capital antes de la compra")
        return None

    logging.info(f"Enviando orden de COMPRA para {symbol} por ${amount:.2f} USD...")
    try:
        req = MarketOrderRequest(
            symbol=symbol,
            notional=amount,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(req)
        logging.info(f"Orden enviada. ID: {order.id}. Esperando ejecución...")

        try:
            filled = wait_for_order_fill(trading_client, order.id)
        except TimeoutError:
            logging.error(f"Timeout esperando confirmación de compra ({order.id}). Verificando estado real en Alpaca...")
            filled = resolve_pending_order(trading_client, order.id)
            if filled is None:
                logging.error(f"Compra {order.id} cancelada sin ejecución. Nada que registrar.")
                return None
            logging.warning(f"Compra {order.id} se resolvió tras el timeout (fill tardío o parcial).")

        filled_price = float(filled.filled_avg_price)
        filled_qty   = float(filled.filled_qty)
        filled_at    = filled.filled_at.isoformat() if filled.filled_at else datetime.utcnow().isoformat()

        logging.info(f"¡COMPRA COMPLETADA! Precio: ${filled_price:.2f} | Acciones: {filled_qty:.6f}")
        return {"price": filled_price, "qty": filled_qty, "order_id": str(filled.id), "timestamp": filled_at}

    except Exception:
        logging.exception(f"Error al ejecutar compra de {symbol}")
        return None


def resync_state_after_failure(
    trading_client, symbol: str, state: dict, buy_amount: float, max_buys: int, state_file: str
) -> dict:
    """Se llama tras una compra/venta que no devolvió confirmación (timeout u
    otro error). La orden puede haberse ejecutado igual en Alpaca aunque el
    bot no lo haya confirmado a tiempo; sin este resync, el estado local
    queda desincronizado del real y el bot reintenta acciones inválidas
    (p. ej. vender acciones que ya no tiene) en cada ciclo siguiente."""
    reconciled = reconcile_with_broker(trading_client, symbol, state, buy_amount, max_buys)
    if reconciled is None:
        logging.warning(
            "No se pudo reconciliar el estado tras el fallo. Se reintentará en el próximo ciclo."
        )
        return state
    if reconciled is not state:
        save_state(reconciled, state_file)
    return reconciled


def execute_sell(trading_client, symbol: str, qty: float) -> dict | None:
    logging.info(f"Enviando orden de VENTA para {symbol} de {qty:.6f} acciones...")
    try:
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(req)
        logging.info(f"Orden enviada. ID: {order.id}. Esperando ejecución...")

        try:
            filled = wait_for_order_fill(trading_client, order.id)
        except TimeoutError:
            logging.error(f"Timeout esperando confirmación de venta ({order.id}). Verificando estado real en Alpaca...")
            filled = resolve_pending_order(trading_client, order.id)
            if filled is None:
                logging.error(f"Venta {order.id} cancelada sin ejecución. Nada que registrar.")
                return None
            logging.warning(f"Venta {order.id} se resolvió tras el timeout (fill tardío o parcial).")

        filled_price = float(filled.filled_avg_price)
        filled_qty   = float(filled.filled_qty)
        filled_at    = filled.filled_at.isoformat() if filled.filled_at else datetime.utcnow().isoformat()

        logging.info(f"¡VENTA COMPLETADA! Precio: ${filled_price:.2f} | Acciones: {filled_qty:.6f}")
        return {"price": filled_price, "qty": filled_qty, "order_id": str(filled.id), "timestamp": filled_at}

    except Exception:
        logging.exception(f"Error al ejecutar venta de {symbol}")
        return None


def main():
    load_dotenv()

    # Parámetros: env var tiene prioridad, CLI es fallback
    parser = argparse.ArgumentParser(description="Bot de Grid Trading genérico")
    parser.add_argument("--symbol",        type=str,   default=None)
    parser.add_argument("--buy-amount",    type=float, default=None)
    parser.add_argument("--max-buys",      type=int,   default=None)
    parser.add_argument("--buy-drop-pct",  type=float, default=None)
    parser.add_argument("--sell-rise-pct", type=float, default=None)
    parser.add_argument("--interval",      type=int,   default=None)
    parser.add_argument("--paper",         action="store_true", default=None)
    args = parser.parse_args()

    def get(arg_val, env_key, cast, default):
        if arg_val is not None:
            return arg_val
        env_val = os.getenv(env_key)
        if env_val is not None:
            return cast(env_val)
        return default

    symbol        = (args.symbol or os.getenv("SYMBOL", "")).upper()
    buy_amount    = get(args.buy_amount,    "BUY_AMOUNT",    float, 1000.0)
    max_buys      = get(args.max_buys,      "MAX_BUYS",      int,   10)
    buy_drop_pct  = get(args.buy_drop_pct,  "BUY_DROP_PCT",  float, 0.03)
    sell_rise_pct = get(args.sell_rise_pct, "SELL_RISE_PCT", float, 0.03)
    interval      = get(args.interval,      "INTERVAL",      int,   1200)
    paper_env     = os.getenv("PAPER", "true").lower() != "false"
    paper         = args.paper if args.paper else paper_env

    if not symbol:
        print("Error: debes indicar el símbolo via --symbol o la variable de entorno SYMBOL")
        return
    setup_logging(symbol)

    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        logging.error("Credenciales de Alpaca faltantes en .env. Finalizando.")
        return

    logging.info(f"=== Iniciando Grid Bot: {symbol} ===")
    logging.info(f"  buy_amount    : ${buy_amount:,.2f}")
    logging.info(f"  max_buys      : {max_buys}")
    logging.info(f"  buy_drop_pct  : {buy_drop_pct*100:.1f}%")
    logging.info(f"  sell_rise_pct : {sell_rise_pct*100:.1f}%")
    logging.info(f"  intervalo     : {interval}s")
    logging.info(f"  modo          : {'PAPER' if paper else 'REAL'}")

    trading_client = TradingClient(api_key, secret_key, paper=paper)
    data_client    = StockHistoricalDataClient(api_key, secret_key)

    try:
        account = trading_client.get_account()
        logging.info(f"Conexión exitosa. Cuenta: #{account.account_number}")
        logging.info(f"Efectivo: ${float(account.cash):,.2f} | Portafolio: ${float(account.portfolio_value):,.2f}")
    except Exception:
        logging.exception("Error al conectar con Alpaca")
        return

    data_dir   = os.getenv("DATA_DIR", ".")
    os.makedirs(data_dir, exist_ok=True)
    state_file = os.path.join(data_dir, f"tradebot_{symbol}_state.json")
    state      = load_state(state_file)
    purchases  = state["purchases"]

    logging.info(f"Estado cargado. Compras activas: {len(purchases)}")
    for i, p in enumerate(purchases):
        logging.info(f"  [{i+1}] ${p['price']:.2f} | {p['qty']:.6f} acc | {p['timestamp']}")

    reconciled = reconcile_with_broker(trading_client, symbol, state, buy_amount, max_buys)
    if reconciled is None:
        logging.error("Verificación/reconciliación con Alpaca falló. Deteniendo el bot.")
        return
    if reconciled is not state:
        state = reconciled
        save_state(state, state_file)
    purchases = state["purchases"]
    logging.info("Estado verificado/reconciliado contra Alpaca.")

    while True:
        try:
            clock = trading_client.get_clock()
            if not clock.is_open:
                next_open = clock.next_open.strftime("%Y-%m-%d %H:%M:%S %Z")
                logging.info(f"Mercado cerrado. Próxima apertura: {next_open}.")
                time.sleep(interval)
                continue

            state     = load_state(state_file)
            purchases = state["purchases"]

            current_price = get_latest_price(data_client, symbol)
            if current_price is None:
                logging.warning("No se pudo obtener el precio. Reintentando en el próximo ciclo...")
                time.sleep(interval)
                continue

            logging.info(f"Precio actual de {symbol}: ${current_price:.2f}")

            if len(purchases) == 0:
                logging.info("Sin compras registradas. Ejecutando compra inicial...")
                free_slots = max_buys - len(purchases)
                pool = state.get("profit_pool", 0.0)
                bonus = floor2(pool / free_slots) if free_slots > 0 else 0.0
                effective_buy = floor2(buy_amount + bonus)
                if bonus > 0:
                    logging.info(f"Pool de ganancias: ${pool:.2f} | Bonus esta compra: ${bonus:.2f} | Total: ${effective_buy:.2f}")
                buy_info = execute_buy(trading_client, symbol, effective_buy)
                if buy_info:
                    state["profit_pool"] = pool - bonus
                    purchases.append(buy_info)
                    save_state(state, state_file)
                    logging.info(f"Grid iniciado. Compra a ${buy_info['price']:.2f}. Pool restante: ${state['profit_pool']:.2f}")
                else:
                    logging.error("Compra inicial FALLÓ. El grid no pudo iniciarse. Ver errores anteriores.")
                    state = resync_state_after_failure(trading_client, symbol, state, buy_amount, max_buys, state_file)
                time.sleep(interval)
                continue

            last_purchase  = purchases[-1]
            last_buy_price = last_purchase["price"]
            buy_target     = last_buy_price * (1.0 - buy_drop_pct)
            sell_target    = last_buy_price * (1.0 + sell_rise_pct)

            logging.info(f"-> Última compra: ${last_buy_price:.2f} | Activas: {len(purchases)}/{max_buys} | Pool: ${state.get('profit_pool', 0.0):.2f}")
            logging.info(f"-> Objetivo COMPRA: ${buy_target:.2f} | Objetivo VENTA: ${sell_target:.2f}")

            if current_price <= buy_target:
                if len(purchases) < max_buys:
                    logging.info(f"¡Condición de COMPRA! ${current_price:.2f} <= ${buy_target:.2f}")
                    free_slots = max_buys - len(purchases)
                    pool = state.get("profit_pool", 0.0)
                    bonus = floor2(pool / free_slots) if free_slots > 0 else 0.0
                    effective_buy = floor2(buy_amount + bonus)
                    if bonus > 0:
                        logging.info(f"Pool de ganancias: ${pool:.2f} | Bonus esta compra: ${bonus:.2f} | Total: ${effective_buy:.2f}")
                    buy_info = execute_buy(trading_client, symbol, effective_buy)
                    if buy_info:
                        state["profit_pool"] = pool - bonus
                        purchases.append(buy_info)
                        save_state(state, state_file)
                        logging.info(f"Compra registrada a ${buy_info['price']:.2f}. Activas: {len(purchases)}. Pool restante: ${state['profit_pool']:.2f}")
                    else:
                        logging.error("Compra grid FALLÓ. Ver errores anteriores.")
                        state = resync_state_after_failure(trading_client, symbol, state, buy_amount, max_buys, state_file)
                else:
                    logging.warning(f"Precio cayó a ${current_price:.2f} pero hay {max_buys} compras activas. Sin acción.")

            elif current_price >= sell_target:
                logging.info(f"¡Condición de VENTA! ${current_price:.2f} >= ${sell_target:.2f}")
                sell_info = execute_sell(trading_client, symbol, last_purchase["qty"])
                if sell_info:
                    removed = purchases.pop()
                    cost_basis = removed["price"] * sell_info["qty"]
                    proceeds   = sell_info["price"] * sell_info["qty"]
                    profit     = proceeds - cost_basis
                    if profit > 0:
                        state["profit_pool"] = state.get("profit_pool", 0.0) + profit
                        logging.info(f"Ganancia de ${profit:.2f} sumada al pool. Pool total: ${state['profit_pool']:.2f}")
                    else:
                        logging.info(f"Venta sin ganancia neta (${profit:.2f}). Pool sin cambios: ${state.get('profit_pool', 0.0):.2f}")
                    save_state(state, state_file)
                    logging.info(f"Venta del lote a ${removed['price']:.2f} completada.")
                    if purchases:
                        logging.info(f"Referencia regresa a: ${purchases[-1]['price']:.2f}")
                    else:
                        logging.info("Todos los lotes vendidos. Grid se reiniciará en el próximo ciclo.")
                else:
                    logging.error("Venta FALLÓ. Ver errores anteriores.")
                    state = resync_state_after_failure(trading_client, symbol, state, buy_amount, max_buys, state_file)

            else:
                logging.info("Precio dentro del rango. Sin acciones.")

        except Exception:
            logging.exception("Error inesperado en el bucle principal")

        time.sleep(interval)


if __name__ == "__main__":
    main()
