import json
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import __version__
from app.models import Signal

logger = logging.getLogger(__name__)

# this is the only thing exposed to the internet, so it is kept very small
router = APIRouter()


# turns a pydantic error into one readable line, like "qty: Input should be greater than or equal to 1"
def describe(error):
    parts = []
    for problem in error.errors():
        where = ".".join(str(p) for p in problem["loc"])
        message = problem["msg"].removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)


@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


# tradingview posts here. the token in the url is the password.
@router.post("/hook/{token}")
async def hook(token: str, request: Request):
    ctx = request.app.state.ctx

    # a wrong token looks exactly like a page that does not exist
    if not secrets.compare_digest(token.encode(), ctx.webhook_token.encode()):
        raise HTTPException(status_code=404, detail="Not Found")

    # tradingview sends text/plain when the message is not valid json, so read the raw body
    body = await request.body()
    if len(body) > 10_000:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="the body is not valid json")

    try:
        signal = Signal.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=describe(exc))

    record, should_run = await ctx.processor.receive(signal, "webhook")
    if not should_run:
        return JSONResponse({"signal_id": record.id, "status": record.status}, status_code=200)

    # answer right away (tradingview gives up after 3 seconds), the orders run in the background
    ctx.spawn(ctx.processor.execute(record.id, signal))
    return JSONResponse({"signal_id": record.id, "status": record.status}, status_code=202)
