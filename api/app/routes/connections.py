from fastapi import APIRouter, Depends, HTTPException, Response

from app.deps import get_ctx, require_login
from app.models import ConnectionConfig, ConnectionHealth, TargetsUpdate
from app.tradovate.client import TradovateError

router = APIRouter(prefix="/connections", tags=["connections"], dependencies=[Depends(require_login)])


@router.get("", response_model=list[ConnectionHealth])
async def list_connections(ctx=Depends(get_ctx)):
    return ctx.manager.health()


# logs in to tradovate first, the connection is only saved if the login works
@router.post("", response_model=ConnectionHealth, status_code=201)
async def add_connection(config: ConnectionConfig, ctx=Depends(get_ctx)):
    try:
        conn = await ctx.manager.add(config)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except TradovateError as exc:
        raise HTTPException(status_code=400, detail=f"login failed, connection not saved: {exc}")
    ctx.events.publish("connections")
    return conn.health()


# choose which accounts of this login receive orders
@router.patch("/{label}", response_model=ConnectionHealth)
async def update_targets(label: str, body: TargetsUpdate, ctx=Depends(get_ctx)):
    conn = await ctx.manager.set_targets(label, body.target_accounts)
    if conn is None:
        raise HTTPException(status_code=404, detail="no such connection")
    ctx.events.publish("connections")
    return conn.health()


# try to log in again, for example after a wrong password was fixed on tradovate's side
@router.post("/{label}/reconnect", response_model=ConnectionHealth)
async def reconnect(label: str, ctx=Depends(get_ctx)):
    try:
        conn = await ctx.manager.reconnect(label)
    except TradovateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if conn is None:
        raise HTTPException(status_code=404, detail="no such connection")
    ctx.events.publish("connections")
    return conn.health()


@router.delete("/{label}", status_code=204)
async def delete_connection(label: str, ctx=Depends(get_ctx)):
    if not await ctx.manager.remove(label):
        raise HTTPException(status_code=404, detail="no such connection")
    ctx.events.publish("connections")
    return Response(status_code=204)
