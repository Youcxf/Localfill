import asyncio


async def wait_until(check, seconds=3.0):
    for _ in range(int(seconds / 0.02)):
        if check():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_an_offline_connection_logs_in_again_by_itself(ctx, connected):
    conn = ctx.manager.live["A"]
    # pretend the login failed at startup
    await conn.stop()
    conn.account_names.clear()
    assert not conn.online

    ctx.settings.reconnect_seconds = 0.05
    ctx.manager.start_background()

    assert await wait_until(lambda: conn.online)
    assert len(conn.account_names) == 2


async def test_a_refused_login_is_not_tried_again(ctx, connected):
    conn = ctx.manager.live["A"]
    await conn.stop()
    conn.needs_user = True
    logins_before = len(ctx.broker.logins)

    ctx.settings.reconnect_seconds = 0.05
    ctx.manager.start_background()
    await asyncio.sleep(0.3)

    assert not conn.online
    assert len(ctx.broker.logins) == logins_before
