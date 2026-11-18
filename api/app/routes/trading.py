from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.deps import get_ctx, require_login, require_session, source_of
from app.models import (
    AccountState,
    BatchBody,
    BatchRecord,
    FillRecord,
    FlattenBody,
    FrontContract,
    HaltResponse,
    PnlResponse,
    PositionsResponse,
    Signal,
    SignalCounts,
    SignalRecord,
    SignalStatus,
)
from app.services.batches import BatchHalted, BatchRefused
from app.tradovate.client import TradovateError

router = APIRouter(tags=["trading"], dependencies=[Depends(require_login)])


@router.get("/positions", response_model=PositionsResponse)
async def positions(ctx=Depends(get_ctx)):
    return await ctx.reports.positions()


@router.get("/pnl", response_model=PnlResponse)
async def pnl(ctx=Depends(get_ctx)):
    return await ctx.reports.pnl()


# the real balance, positions and risk state of every target account (?all=true: every account
# that is not blocked)
@router.get("/accounts", response_model=list[AccountState])
async def accounts(every: bool = Query(False, alias="all"), ctx=Depends(get_ctx)):
    return await ctx.reports.accounts(every=every)


# every fill localfill has seen with a seq above since, oldest first. a program keeps the
# last seq it handled and reads on from there after a restart
@router.get("/fills", response_model=list[FillRecord])
async def fills(since: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=1000), ctx=Depends(get_ctx)):
    return await ctx.fills.since(since, limit)


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


# the signal sent with this client_id
@router.get("/signals/client/{client_id}", response_model=SignalRecord)
async def signal_by_client_id(client_id: str, ctx=Depends(get_ctx)):
    record = await ctx.processor.by_client_id(client_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no signal with this client_id")
    return record


@router.get("/signals/{signal_id}", response_model=SignalRecord)
async def get_signal(signal_id: int, ctx=Depends(get_ctx)):
    record = await ctx.processor.get(signal_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such signal")
    return record


# send an order from the dashboard. same body as the tradingview webhook,
# but here we wait for the orders to finish and return the full result.
# a client_id that was sent before returns that signal and sends nothing
@router.post("/signals", response_model=SignalRecord)
async def send_signal(signal: Signal, request: Request, ctx=Depends(get_ctx)):
    record, should_run = await ctx.processor.receive(signal, source_of(request))
    if not should_run:
        return record
    return await ctx.processor.execute(record.id, signal)


# different orders on different accounts, all at the same time. everything is checked first:
# one bad order and nothing is sent (422 with the reasons)
@router.post("/batches", response_model=BatchRecord)
async def send_batch(body: BatchBody, request: Request, ctx=Depends(get_ctx)):
    try:
        return await ctx.batches.send(body, source_of(request))
    except BatchHalted as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BatchRefused as exc:
        raise HTTPException(status_code=422, detail=exc.reasons)


@router.get("/batches/client/{client_id}", response_model=BatchRecord)
async def batch_by_client_id(client_id: str, ctx=Depends(get_ctx)):
    record = await ctx.batches.by_client_id(client_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no batch with this client_id")
    return record


@router.get("/batches/{batch_id}", response_model=BatchRecord)
async def get_batch(batch_id: int, ctx=Depends(get_ctx)):
    record = await ctx.batches.get(batch_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such batch")
    return record


# the panic button: close everything (or just one symbol)
@router.post("/flatten", response_model=SignalRecord)
async def flatten(body: FlattenBody, request: Request, ctx=Depends(get_ctx)):
    signal = Signal(action="flatten", symbol=body.symbol, connection=body.connection)
    record, _ = await ctx.processor.receive(signal, source_of(request))
    return await ctx.processor.execute(record.id, signal)


# the kill switch: refuses every new buy and sell until resume, ends the queued ones and
# flattens every target account right away, without waiting for the queue
@router.post("/halt", response_model=HaltResponse)
async def halt(request: Request, ctx=Depends(get_ctx)):
    source = source_of(request)
    was_running = bool(ctx.processor.running)
    record = await ctx.processor.halt(source)
    if was_running:
        # the signal that was running may still open something, flatten again once it is over
        ctx.spawn(ctx.processor.flatten_after_queue(source))
    return HaltResponse(halted=True, flatten=record)


# only from the dashboard, a program can not undo a halt
@router.post("/resume", response_model=HaltResponse, dependencies=[Depends(require_session)])
async def resume(ctx=Depends(get_ctx)):
    await ctx.processor.resume()
    return HaltResponse(halted=False)


# the contract a symbol trades right now, like MNQ -> MNQZ6. it changes at rollover
@router.get("/contracts/front", response_model=FrontContract)
async def front_contract(symbol: str = Query(min_length=1), ctx=Depends(get_ctx)):
    conns = [conn for conn in ctx.manager.pick() if conn.online]
    if not conns:
        raise HTTPException(status_code=503, detail="no connection is online")
    # the same clean up as an alert gets: CME_MINI:MNQ1! -> MNQ
    symbol = Signal(action="flatten", symbol=symbol).symbol
    try:
        contract = await ctx.resolver.lookup(conns[0], symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TradovateError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return FrontContract(symbol=symbol, name=contract["name"], id=contract.get("id") or 0)
