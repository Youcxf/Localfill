from fastapi import APIRouter, Depends

from app import __version__
from app.context import new_webhook_token
from app.deps import get_ctx, require_login
from app.models import SettingsResponse, SettingsUpdate, StatusResponse

router = APIRouter(tags=["system"])


# no login needed, used by the docker healthcheck
@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


# a quick summary for the top of the dashboard
@router.get("/status", response_model=StatusResponse, dependencies=[Depends(require_login)])
async def status(ctx=Depends(get_ctx)):
    connections = ctx.manager.health()
    return StatusResponse(
        version=__version__,
        dry_run=await ctx.db.get_setting("dry_run", "0") == "1",
        connections_total=len(connections),
        connections_online=sum(1 for c in connections if c.authenticated),
    )


async def build_settings(ctx):
    path = f"/hook/{ctx.webhook_token}"
    url = None
    if ctx.settings.public_url:
        url = ctx.settings.public_url.rstrip("/") + path
    return SettingsResponse(
        dry_run=await ctx.db.get_setting("dry_run", "0") == "1",
        webhook_token=ctx.webhook_token,
        webhook_path=path,
        webhook_url=url,
    )


@router.get("/settings", response_model=SettingsResponse, dependencies=[Depends(require_login)])
async def get_settings(ctx=Depends(get_ctx)):
    return await build_settings(ctx)


@router.patch("/settings", response_model=SettingsResponse, dependencies=[Depends(require_login)])
async def update_settings(body: SettingsUpdate, ctx=Depends(get_ctx)):
    if body.dry_run is not None:
        await ctx.db.set_setting("dry_run", "1" if body.dry_run else "0")
        ctx.events.publish("settings")
    return await build_settings(ctx)


# makes a new webhook url. the old one stops working right away
@router.post("/settings/webhook-token/regenerate", response_model=SettingsResponse, dependencies=[Depends(require_login)])
async def regenerate_token(ctx=Depends(get_ctx)):
    token = new_webhook_token()
    await ctx.db.set_setting("webhook_token", token)
    ctx.webhook_token = token
    ctx.events.publish("settings")
    return await build_settings(ctx)
