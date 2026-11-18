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


# true if the request carries a valid api token: "Authorization: Bearer <token>"
async def has_api_token(request, ctx):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return False
    return await ctx.tokens.check(token.strip()) is not None


# put this on any route that needs the admin to be logged in, or a program with an api token
async def require_login(request: Request, ctx=Depends(get_ctx)):
    if is_logged_in(request, ctx):
        request.state.via = "session"
        return
    if await has_api_token(request, ctx):
        request.state.via = "token"
        return
    raise HTTPException(status_code=401, detail="not logged in")


# for things a program must never do (unblock an account, make tokens, add connections...).
# only the dashboard's session cookie works here, an api token gets 403
async def require_session(request: Request, ctx=Depends(get_ctx)):
    if is_logged_in(request, ctx):
        return
    if await has_api_token(request, ctx):
        raise HTTPException(status_code=403, detail="an api token can not do this, log in to the dashboard")
    raise HTTPException(status_code=401, detail="not logged in")


# "api" for programs using a token, "dashboard" for the browser. saved with each signal
def source_of(request):
    return "api" if getattr(request.state, "via", None) == "token" else "dashboard"
