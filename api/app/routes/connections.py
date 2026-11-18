from fastapi import APIRouter, Depends, HTTPException, Response

from app.deps import get_ctx, require_login, require_session
from app.models import AccountChange, AccountLimitsUpdate, BlockedUpdate, ConnectionConfig, ConnectionHealth, TargetsUpdate
from app.tradovate.client import TradovateError

router = APIRouter(prefix="/connections", tags=["connections"], dependencies=[Depends(require_login)])


def found(conn):
    if conn is None:
        raise HTTPException(status_code=404, detail="no such connection")
    return conn


@router.get("", response_model=list[ConnectionHealth])
async def list_connections(ctx=Depends(get_ctx)):
    return ctx.manager.health()


# logs in to tradovate first, the connection is only saved if the login works.
# only from the dashboard, never with an api token
@router.post("", response_model=ConnectionHealth, status_code=201, dependencies=[Depends(require_session)])
async def add_connection(config: ConnectionConfig, ctx=Depends(get_ctx)):
    try:
        conn = await ctx.manager.add(config)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except TradovateError as exc:
        raise HTTPException(status_code=400, detail=f"login failed, connection not saved: {exc}")
    ctx.events.publish("connections")
    return conn.health()


# choose which accounts of this login receive orders. a blocked account can not be one of them
@router.patch("/{label}", response_model=ConnectionHealth)
async def update_targets(label: str, body: TargetsUpdate, ctx=Depends(get_ctx)):
    try:
        conn = found(await ctx.manager.set_targets(label, body.target_accounts))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ctx.events.publish("connections")
    return conn.health()


# makes one account a target without sending the whole list, so two people (or you and a
# program) changing targets at the same time can not undo each other
@router.post("/{label}/targets/add", response_model=ConnectionHealth)
async def add_target(label: str, body: AccountChange, ctx=Depends(get_ctx)):
    try:
        conn = found(await ctx.manager.add_target(label, body.account))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ctx.events.publish("connections")
    return conn.health()


@router.post("/{label}/targets/remove", response_model=ConnectionHealth)
async def remove_target(label: str, body: AccountChange, ctx=Depends(get_ctx)):
    try:
        conn = found(await ctx.manager.remove_target(label, body.account))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ctx.events.publish("connections")
    return conn.health()


# accounts that are never traded or flattened, like your personal account on the same login.
# only from the dashboard, so a program can never unblock one
@router.put("/{label}/blocked", response_model=ConnectionHealth, dependencies=[Depends(require_session)])
async def set_blocked(label: str, body: BlockedUpdate, ctx=Depends(get_ctx)):
    conn = found(await ctx.manager.set_blocked(label, body.blocked_accounts))
    ctx.events.publish("connections")
    return conn.health()


# one account's own size limits, like a prop firm's scaling plan. only from the dashboard,
# so a program can not raise them
@router.put("/{label}/limits", response_model=ConnectionHealth, dependencies=[Depends(require_session)])
async def set_limits(label: str, body: AccountLimitsUpdate, ctx=Depends(get_ctx)):
    try:
        conn = found(await ctx.manager.set_limits(label, body.account, body.max_contracts))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ctx.events.publish("connections")
    return conn.health()


# reloads the account list now, so an account you just bought shows up (as is_new)
@router.post("/{label}/refresh", response_model=ConnectionHealth)
async def refresh(label: str, ctx=Depends(get_ctx)):
    try:
        conn = found(await ctx.manager.refresh(label))
    except TradovateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ctx.events.publish("connections")
    return conn.health()


# try to log in again, for example after a wrong password was fixed on tradovate's side
@router.post("/{label}/reconnect", response_model=ConnectionHealth)
async def reconnect(label: str, ctx=Depends(get_ctx)):
    try:
        conn = await ctx.manager.reconnect(label)
    except TradovateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    found(conn)
    ctx.events.publish("connections")
    return conn.health()


@router.delete("/{label}", status_code=204, dependencies=[Depends(require_session)])
async def delete_connection(label: str, ctx=Depends(get_ctx)):
    if not await ctx.manager.remove(label):
        raise HTTPException(status_code=404, detail="no such connection")
    ctx.events.publish("connections")
    return Response(status_code=204)
