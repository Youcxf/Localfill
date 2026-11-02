from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_ctx, require_login
from app.models import FlattenBody, PnlResponse, PositionsResponse, Signal, SignalCounts, SignalRecord, SignalStatus

router = APIRouter(tags=["trading"], dependencies=[Depends(require_login)])


@router.get("/positions", response_model=PositionsResponse)
async def positions(ctx=Depends(get_ctx)):
    return await ctx.reports.positions()


@router.get("/pnl", response_model=PnlResponse)
async def pnl(ctx=Depends(get_ctx)):
    return await ctx.reports.pnl()


# the history, newest first. add ?status=done to only get one state
@router.get("/signals", response_model=list[SignalRecord])
async def list_signals(
    status: SignalStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx=Depends(get_ctx),
):
    return await ctx.processor.list_recent(limit, offset, status)


# how many signals are in each state (this has to be above /signals/{signal_id})
@router.get("/signals/counts", response_model=SignalCounts)
async def signal_counts(ctx=Depends(get_ctx)):
    return await ctx.processor.counts()


@router.get("/signals/{signal_id}", response_model=SignalRecord)
async def get_signal(signal_id: int, ctx=Depends(get_ctx)):
    record = await ctx.processor.get(signal_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such signal")
    return record


# send an order from the dashboard. same body as the tradingview webhook,
# but here we wait for the orders to finish and return the full result
@router.post("/signals", response_model=SignalRecord)
async def send_signal(signal: Signal, ctx=Depends(get_ctx)):
    record, _ = await ctx.processor.receive(signal, "dashboard")
    return await ctx.processor.execute(record.id, signal)


# the panic button: close everything (or just one symbol)
@router.post("/flatten", response_model=SignalRecord)
async def flatten(body: FlattenBody, ctx=Depends(get_ctx)):
    signal = Signal(action="flatten", symbol=body.symbol, connection=body.connection)
    record, _ = await ctx.processor.receive(signal, "dashboard")
    return await ctx.processor.execute(record.id, signal)
