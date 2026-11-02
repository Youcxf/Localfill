import time

from fastapi import Depends, HTTPException, Request

SESSION_COOKIE = "localfill_session"


# gives a route access to the shared context
def get_ctx(request: Request):
    return request.app.state.ctx


def is_logged_in(request, ctx):
    token = request.cookies.get(SESSION_COOKIE)
    expires = ctx.sessions.get(token)
    return expires is not None and expires > time.time()


# put this on any route that needs the admin to be logged in
def require_login(request: Request, ctx=Depends(get_ctx)):
    if not is_logged_in(request, ctx):
        raise HTTPException(status_code=401, detail="not logged in")
