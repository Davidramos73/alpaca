import json
from unittest.mock import MagicMock

from alpaca.common.exceptions import APIError

from tradebot import resync_state_after_failure


def make_broker_stub(open_orders=None, position_qty=None, avg_entry_price=None):
    """Simula un trading_client donde, tras un timeout del bot, la orden ya
    se ejecutó del lado de Alpaca (o no, según los args)."""
    stub = MagicMock()
    stub.get_orders.return_value = open_orders or []
    if position_qty is None:
        stub.get_open_position.side_effect = APIError({"message": "position does not exist"})
    else:
        position = MagicMock()
        position.qty = position_qty
        position.avg_entry_price = avg_entry_price
        stub.get_open_position.return_value = position
    return stub


def test_resync_clears_phantom_position_after_timed_out_sell(tmp_path):
    """Reproduce el bug real: el bot cree que tiene 6.232476 acciones porque
    la venta 'dio timeout', pero la orden ya se llenó en Alpaca y la posición
    real es 0. resync_state_after_failure debe vaciar `purchases` para que el
    bot deje de reintentar una venta imposible (que Alpaca rechaza con
    'fractional orders cannot be sold short')."""
    state_file = tmp_path / "state.json"
    state = {
        "purchases": [
            {"price": 160.92, "qty": 6.232476, "order_id": "orig", "timestamp": "2026-07-06T12:00:00"}
        ],
        "profit_pool": 42.24,
    }
    stub = make_broker_stub(position_qty=None)  # sin posición: ya se vendió todo

    result = resync_state_after_failure(stub, "SPCX", state, buy_amount=1000.0, max_buys=10, state_file=str(state_file))

    assert result["purchases"] == []
    saved = json.loads(state_file.read_text())
    assert saved["purchases"] == []


def test_resync_keeps_state_when_broker_matches_local(tmp_path):
    """Si la posición real coincide con la local (p.ej. una compra falló de
    verdad y no llegó a ejecutarse), no debe reescribirse el estado."""
    state_file = tmp_path / "state.json"
    state = {
        "purchases": [
            {"price": 160.92, "qty": 6.232476, "order_id": "orig", "timestamp": "2026-07-06T12:00:00"}
        ],
        "profit_pool": 42.24,
    }
    stub = make_broker_stub(position_qty="6.232476", avg_entry_price="160.92")

    result = resync_state_after_failure(stub, "SPCX", state, buy_amount=1000.0, max_buys=10, state_file=str(state_file))

    assert result is state
    assert not state_file.exists()


def test_resync_does_not_crash_when_open_orders_pending(tmp_path):
    """Si hay una orden abierta sin resolver, reconcile_with_broker devuelve
    None (ambiguo). A diferencia del arranque, esto NO debe detener el bot:
    debe conservar el estado anterior y reintentar en el próximo ciclo."""
    state_file = tmp_path / "state.json"
    state = {"purchases": [], "profit_pool": 0.0}
    open_order = MagicMock()
    open_order.id = "still-open-order"
    stub = make_broker_stub(open_orders=[open_order])

    result = resync_state_after_failure(stub, "SPCX", state, buy_amount=1000.0, max_buys=10, state_file=str(state_file))

    assert result is state
    assert not state_file.exists()


# ---------------------------------------------------------------------------
# Reconstrucción de lotes desde el historial de fills de Alpaca
# ---------------------------------------------------------------------------
from tradebot import replay_fills_lifo, reconcile_with_broker


def _fill(side, qty, price, order_id, ts):
    return {"side": side, "qty": qty, "price": price, "order_id": order_id, "timestamp": ts}


def _fill_api(side, qty, price, order_id, ts, symbol="TSLA", act_id="a1"):
    """Dict con la forma que devuelve GET /v2/account/activities/FILL."""
    return {"symbol": symbol, "side": side, "qty": str(qty), "price": str(price),
            "order_id": order_id, "transaction_time": ts, "id": act_id}


def test_replay_reconstruye_lotes_reales_con_ventas_lifo():
    """Compras en bajada (418, 412, 408) y una venta LIFO parcial: deben
    quedar abiertos los lotes viejos caros con sus precios REALES, no un
    promedio."""
    fills = [
        _fill("buy", 2.4, 418.09, "o1", "2026-07-06T16:16:00Z"),
        _fill("buy", 2.4, 412.91, "o2", "2026-07-07T13:54:00Z"),
        _fill("buy", 2.4, 408.42, "o3", "2026-07-07T14:16:00Z"),
        _fill("sell", 2.4, 411.00, "o4", "2026-07-09T14:01:00Z"),  # vende el último (408.42)
    ]
    lots = replay_fills_lifo(fills, broker_qty=4.8)
    assert lots is not None
    assert [l["price"] for l in lots] == [418.09, 412.91]
    assert abs(sum(l["qty"] for l in lots) - 4.8) < 1e-9


def test_replay_fusiona_fills_parciales_de_la_misma_orden():
    """Alpaca parte una compra de 2.464 acc en fills de 1 + 1 + 0.464 con el
    mismo order_id: deben fusionarse en UN lote (precio promedio ponderado)."""
    fills = [
        _fill("buy", 1.0, 400.00, "o1", "2026-07-08T13:33:00Z"),
        _fill("buy", 1.0, 400.00, "o1", "2026-07-08T13:33:01Z"),
        _fill("buy", 0.464, 400.10, "o1", "2026-07-08T13:33:01Z"),
    ]
    lots = replay_fills_lifo(fills, broker_qty=2.464)
    assert lots is not None
    assert len(lots) == 1
    assert abs(lots[0]["qty"] - 2.464) < 1e-9
    assert 400.00 < lots[0]["price"] < 400.10


def test_replay_devuelve_none_si_no_cuadra():
    """Historial incompleto (venta de acciones que los fills no explican) o
    posición que no coincide: None, para que reconcile use el fallback."""
    fills = [
        _fill("buy", 1.0, 400.00, "o1", "2026-07-08T13:33:00Z"),
        _fill("sell", 2.0, 405.00, "o2", "2026-07-09T13:33:00Z"),
    ]
    assert replay_fills_lifo(fills, broker_qty=1.0) is None
    fills2 = [_fill("buy", 1.0, 400.00, "o1", "2026-07-08T13:33:00Z")]
    assert replay_fills_lifo(fills2, broker_qty=5.0) is None


def test_reconcile_usa_fills_reales_en_discrepancia(tmp_path):
    """Reproduce el incidente del 2026-07-10: estado local vacío, posición
    real en Alpaca. Con el historial de fills disponible, los lotes deben
    reconstruirse con los precios reales de compra, no todos al promedio."""
    stub = make_broker_stub(position_qty="4.8", avg_entry_price="415.50")
    stub.get.return_value = [
        _fill_api("buy", 2.4, 418.09, "o1", "2026-07-06T16:16:00Z", act_id="a1"),
        _fill_api("buy", 2.4, 412.91, "o2", "2026-07-07T13:54:00Z", act_id="a2"),
    ]
    state = {"purchases": [], "profit_pool": 10.0}

    result = reconcile_with_broker(stub, "TSLA", state, buy_amount=1000.0, max_buys=10)

    assert result is not None
    assert sorted(p["price"] for p in result["purchases"]) == [412.91, 418.09]
    assert result["profit_pool"] == 0.0


def test_reconcile_cae_al_promedio_si_fills_no_cuadran(tmp_path):
    """Si el historial no explica la posición (trades manuales, historial
    truncado), debe mantenerse el fallback actual: lotes al precio promedio."""
    stub = make_broker_stub(position_qty="4.8", avg_entry_price="415.50")
    stub.get.return_value = [
        _fill_api("buy", 1.0, 418.09, "o1", "2026-07-06T16:16:00Z", act_id="a1"),
    ]
    state = {"purchases": [], "profit_pool": 0.0}

    result = reconcile_with_broker(stub, "TSLA", state, buy_amount=1000.0, max_buys=10)

    assert result is not None
    assert all(p["price"] == 415.50 for p in result["purchases"])
    assert abs(sum(p["qty"] for p in result["purchases"]) - 4.8) < 1e-6
