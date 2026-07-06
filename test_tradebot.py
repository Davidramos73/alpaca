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
