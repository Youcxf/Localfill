import asyncio

from app.context import AppContext
from app.models import ConnectionConfig, Signal

from .conftest import make_settings


async def test_running_signals_finish_before_shutdown(tmp_path, broker_server):
    broker, server = broker_server
    ctx = AppContext(make_settings(tmp_path, server))
    await ctx.start()
    await ctx.manager.add(ConnectionConfig(label="A", username="someone", password="secret123"))

    # every order takes a while, so it is still in flight when we shut down
    broker.order_delay = 0.3
    signal = Signal(action="buy", symbol="MNQ")
    record, _ = await ctx.processor.receive(signal, "webhook")
    ctx.spawn(ctx.processor.execute(record.id, signal))
    await asyncio.sleep(0.1)
    # nothing has filled yet
    assert broker.positions == {}

    await ctx.stop()

    # stop() waited: both accounts got their order
    assert len(broker.positions) == 2
    assert all(p["netPos"] == 1 for p in broker.positions.values())
