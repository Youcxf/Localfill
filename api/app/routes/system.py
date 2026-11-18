from fastapi import APIRouter, Depends

from app import __version__
from app.context import new_webhook_token
from app.deps import get_ctx, require_login, require_session
from app.models import ConnectionStatus, SettingsResponse, SettingsUpdate, StatusResponse

router = APIRouter(tags=["system"])


# no login needed, used by the docker healthcheck
@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


# a quick summary for the top of the dashboard, and for programs that check localfill before trading
@router.get("/status", response_model=StatusResponse, dependencies=[Depends(require_login)])
async def status(ctx=Depends(get_ctx)):
    connections = ctx.manager.health()
    details = []
    for conn in ctx.manager.pick():
        details.append(ConnectionStatus(
            label=conn.label,
            online=conn.online,
            last_fill_poll=ctx.fills.last_poll.get(conn.label),
            last_error=ctx.fills.last_error.get(conn.label) or conn.last_error,
        ))
    return StatusResponse(
        version=__version__,
        dry_run=await ctx.db.get_setting("dry_run", "0") == "1",
        connections_total=len(connections),
        connections_online=sum(1 for c in connections if c.authenticated),
        halted=ctx.processor.halted,
        connections=details,
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
        max_contracts=ctx.settings.max_contracts,
        max_contracts_per_symbol=ctx.settings.symbol_limits,
        daily_flatten_et=ctx.settings.daily_flatten_et or None,
    )


@router.get("/settings", response_model=SettingsResponse, dependencies=[Depends(require_login)])
async def get_settings(ctx=Depends(get_ctx)):
    return await build_settings(ctx)


# only from the dashboard: a program may read dry run, never change it
@router.patch("/settings", response_model=SettingsResponse, dependencies=[Depends(require_session)])
async def update_settings(body: SettingsUpdate, ctx=Depends(get_ctx)):
    if body.dry_run is not None:
        await ctx.db.set_setting("dry_run", "1" if body.dry_run else "0")
        ctx.events.publish("settings")
    return await build_settings(ctx)


# makes a new webhook url. the old one stops working right away
@router.post("/settings/webhook-token/regenerate", response_model=SettingsResponse, dependencies=[Depends(require_session)])
async def regenerate_token(ctx=Depends(get_ctx)):
    token = new_webhook_token()
    await ctx.db.set_setting("webhook_token", token)
    ctx.webhook_token = token
    ctx.events.publish("settings")
    return await build_settings(ctx)
