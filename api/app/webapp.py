import os

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from app import __version__
from app.routes import auth, connections, events, ingress, system, trading


# serves the built angular app. for a url that is not a file (like /connections)
# it sends index.html, so angular's router can take over
class SpaFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # (windows gives backslashes in the path, so fix that before checking)
            is_api = path.replace("\\", "/").startswith("api/")
            if exc.status_code == 404 and not is_api:
                return await super().get_response("index.html", scope)
            raise


# server 1: the public one. only the webhook lives here, no docs, no dashboard
def create_ingress_app(ctx):
    app = FastAPI(title="localfill ingress", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.include_router(ingress.router)
    return app


# server 2: the private one. the json api for the dashboard, plus the dashboard itself
def create_admin_app(ctx):
    app = FastAPI(
        title="localfill admin api",
        version=__version__,
        description="the api behind the localfill dashboard. log in with /api/auth/login first.",
        docs_url=None,
    )
    app.state.ctx = ctx

    for router in (auth.router, system.router, connections.router, trading.router, events.router):
        app.include_router(router, prefix="/api")

    # the docs page is ours, so its browser tab shows the localfill icon (served with the dashboard files)
    @app.get("/docs", include_in_schema=False)
    async def docs():
        return get_swagger_ui_html(openapi_url="/openapi.json", title="localfill api", swagger_favicon_url="/icon.png")

    if os.path.isdir(ctx.settings.static_dir):
        app.mount("/", SpaFiles(directory=ctx.settings.static_dir, html=True), name="web")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"app": "localfill", "message": "the api is running, see /docs. no frontend found in " + ctx.settings.static_dir}

    return app
