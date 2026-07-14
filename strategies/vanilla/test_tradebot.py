import importlib.util
import os
import types
from unittest.mock import Mock

import pytest
from alpaca.trading.enums import OrderStatus


def _load_module():
    path = os.path.join(os.path.dirname(__file__), "tradebot.py")
    spec = importlib.util.spec_from_file_location("tradebot_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
resolve_pending_order = mod.resolve_pending_order
execute_buy = mod.execute_buy
execute_sell = mod.execute_sell


def _order(status, filled_qty=0.0, order_id="order-1", filled_avg_price=None, filled_at=None):
    return types.SimpleNamespace(
        id=order_id,
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
        filled_at=filled_at,
    )


# ---------------------------------------------------------------------------
# resolve_pending_order: la orden en realidad ya se había llenado (solo tardó
# más de lo que esperaba wait_for_order_fill) -> se toma como exitosa y NO se
# cancela nada.
# ---------------------------------------------------------------------------
def test_resolve_pending_order_ya_llenada_no_cancela():
    client = Mock()
    client.get_order_by_id.return_value = _order(OrderStatus.FILLED, filled_qty=2.5, filled_avg_price=100.0)

    result = resolve_pending_order(client, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    client.cancel_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_pending_order: la orden sigue viva -> se cancela, y al re-consultar
# aparece limpiamente cancelada sin fill -> devuelve None (segura para
# reintentar con una orden nueva).
# ---------------------------------------------------------------------------
def test_resolve_pending_order_cancela_limpio_sin_fill():
    client = Mock()
    client.get_order_by_id.side_effect = [
        _order(OrderStatus.NEW, filled_qty=0.0),
        _order(OrderStatus.CANCELED, filled_qty=0.0),
    ]

    result = resolve_pending_order(client, "order-1")

    assert result is None
    client.cancel_order_by_id.assert_called_once_with("order-1")


# ---------------------------------------------------------------------------
# resolve_pending_order: se intenta cancelar pero la orden ganó la carrera y
# se llenó justo antes -> el segundo chequeo (fuente de verdad) debe
# devolverla como ejecutada, no como cancelada.
# ---------------------------------------------------------------------------
def test_resolve_pending_order_fill_gana_la_carrera_con_la_cancelacion():
    client = Mock()
    client.get_order_by_id.side_effect = [
        _order(OrderStatus.NEW, filled_qty=0.0),
        _order(OrderStatus.FILLED, filled_qty=2.5, filled_avg_price=100.0),
    ]

    result = resolve_pending_order(client, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    client.cancel_order_by_id.assert_called_once_with("order-1")


# ---------------------------------------------------------------------------
# resolve_pending_order: la cancelación llega a mitad de un fill parcial ->
# CANCELED con filled_qty > 0. Debe devolver la orden (no None), para que el
# caller contabilice la porción que sí se ejecutó en vez de perderla.
# ---------------------------------------------------------------------------
def test_resolve_pending_order_fill_parcial_tras_cancelar():
    client = Mock()
    client.get_order_by_id.side_effect = [
        _order(OrderStatus.PARTIALLY_FILLED, filled_qty=1.2),
        _order(OrderStatus.CANCELED, filled_qty=1.2, filled_avg_price=100.0),
    ]

    result = resolve_pending_order(client, "order-1")

    assert result is not None
    assert result.filled_qty == 1.2


# ---------------------------------------------------------------------------
# resolve_pending_order: falla la consulta inicial (error de red) -> no se
# asume nada, se devuelve None sin intentar cancelar a ciegas.
# ---------------------------------------------------------------------------
def test_resolve_pending_order_error_de_consulta_no_asume_nada():
    client = Mock()
    client.get_order_by_id.side_effect = Exception("network error")

    result = resolve_pending_order(client, "order-1")

    assert result is None
    client.cancel_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# execute_sell: wait_for_order_fill hace timeout, pero resolve_pending_order
# descubre que en realidad ya se había llenado -> execute_sell debe devolver
# el resultado exitoso igual, sin mandar una segunda orden de venta.
# ---------------------------------------------------------------------------
def test_execute_sell_timeout_pero_orden_ya_llenada_no_reintenta(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mod, "wait_for_order_fill", Mock(side_effect=TimeoutError("timeout")))

    client = Mock()
    submitted_order = _order(OrderStatus.NEW, order_id="sell-1")
    client.submit_order.return_value = submitted_order
    client.get_order_by_id.side_effect = [
        _order(OrderStatus.FILLED, filled_qty=2.5, filled_avg_price=100.0, filled_at=None, order_id="sell-1"),
    ]

    result = execute_sell(client, "TSLA", 2.5)

    assert result is not None
    assert result["qty"] == 2.5
    assert client.submit_order.call_count == 1  # una sola orden enviada, nunca un duplicado
    client.cancel_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# execute_sell: wait_for_order_fill hace timeout, y tras verificar/cancelar
# queda confirmado que no se ejecutó nada -> devuelve None limpiamente (el
# caller podrá reintentar en el próximo ciclo sabiendo que no quedó nada
# pendiente, a diferencia del bug de producción donde dos órdenes
# "abandonadas" terminaron ejecutándose de todos modos).
# ---------------------------------------------------------------------------
def test_execute_sell_timeout_y_cancelacion_limpia_devuelve_none(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mod, "wait_for_order_fill", Mock(side_effect=TimeoutError("timeout")))

    client = Mock()
    submitted_order = _order(OrderStatus.NEW, order_id="sell-2")
    client.submit_order.return_value = submitted_order
    client.get_order_by_id.side_effect = [
        _order(OrderStatus.NEW, filled_qty=0.0, order_id="sell-2"),
        _order(OrderStatus.CANCELED, filled_qty=0.0, order_id="sell-2"),
    ]

    result = execute_sell(client, "TSLA", 2.5)

    assert result is None
    client.cancel_order_by_id.assert_called_once_with("sell-2")
    assert client.submit_order.call_count == 1
