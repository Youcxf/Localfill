import asyncio
import contextlib
import logging
import signal

import uvicorn

from app.config import Settings
from app.context import AppContext
from app.lock import AlreadyRunning
from app.webapp import create_admin_app, create_ingress_app

logger = logging.getLogger("localfill")


# uvicorn normally catches ctrl+c and SIGTERM by itself, but with two servers
# they would fight over it. so we turn that off and handle the signals in run()
class Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


def make_server(app, host, port, log_level):
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        # the webhook token is in the url, so we do not want access logs
        access_log=False,
        lifespan="off",
        # do not wait forever for open event streams when shutting down
        timeout_graceful_shutdown=3,
    )
    return Server(config)


async def run():
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    ctx = AppContext(settings)
    try:
        await ctx.start()
    except AlreadyRunning as exc:
        logger.error("%s", exc)
        raise SystemExit(1)

    ingress = make_server(create_ingress_app(ctx), settings.ingress_host, settings.ingress_port, settings.log_level)
    admin = make_server(create_admin_app(ctx), settings.admin_host, settings.admin_port, settings.log_level)

    def stop():
        logger.info("shutting down")
        ingress.should_exit = True
        admin.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            # windows can't do this, there ctrl+c is handled by asyncio.run
            pass

    logger.info("webhook server on %s:%s", settings.ingress_host, settings.ingress_port)
    logger.info("dashboard and api on %s:%s", settings.admin_host, settings.admin_port)
    try:
        await asyncio.gather(ingress.serve(), admin.serve())
    finally:
        await ctx.stop()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
