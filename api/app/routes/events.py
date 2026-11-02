import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.deps import get_ctx, require_login

router = APIRouter(tags=["events"], dependencies=[Depends(require_login)])


# live updates for the dashboard (server sent events)
# every message is one line: data: {"type": "...", "data": ...}
# types: "signal" (a signal was received or finished), "connections", "settings"
@router.get("/events")
async def events(ctx=Depends(get_ctx)):
    queue = ctx.events.subscribe()

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # a comment line now and then, so nothing closes the idle connection
                    yield ": ping\n\n"
        finally:
            ctx.events.unsubscribe(queue)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)
