import asyncio
import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.deps import SESSION_COOKIE, get_ctx, is_logged_in
from app.models import AuthStatus, LoginBody, SetupBody

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_SECONDS = 7 * 24 * 3600


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + digest.hex()


def check_password(password, stored):
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


# creates a session and puts its id in a cookie
def start_session(ctx, response):
    now = time.time()
    # throw away sessions that expired
    for token in [t for t, expires in ctx.sessions.items() if expires < now]:
        del ctx.sessions[token]
    token = secrets.token_urlsafe(32)
    ctx.sessions[token] = now + SESSION_SECONDS
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict")


# the frontend calls this first to know which screen to show
@router.get("/status", response_model=AuthStatus)
async def status(request: Request, ctx=Depends(get_ctx)):
    stored = await ctx.db.get_setting("admin_password")
    return AuthStatus(setup_required=stored is None, logged_in=is_logged_in(request, ctx))


# first run only: choose the admin password
@router.post("/setup", response_model=AuthStatus)
async def setup(body: SetupBody, response: Response, ctx=Depends(get_ctx)):
    if await ctx.db.get_setting("admin_password") is not None:
        raise HTTPException(status_code=409, detail="the password is already set")
    hashed = await asyncio.to_thread(hash_password, body.password)
    await ctx.db.set_setting("admin_password", hashed)
    start_session(ctx, response)
    return AuthStatus(setup_required=False, logged_in=True)


@router.post("/login", response_model=AuthStatus)
async def login(body: LoginBody, response: Response, ctx=Depends(get_ctx)):
    stored = await ctx.db.get_setting("admin_password")
    if stored is None:
        raise HTTPException(status_code=409, detail="no password yet, use /auth/setup first")
    ok = await asyncio.to_thread(check_password, body.password, stored)
    if not ok:
        # slows down someone who is guessing
        await asyncio.sleep(1)
        raise HTTPException(status_code=401, detail="wrong password")
    start_session(ctx, response)
    return AuthStatus(setup_required=False, logged_in=True)


@router.post("/logout", status_code=204)
async def logout(request: Request, ctx=Depends(get_ctx)):
    ctx.sessions.pop(request.cookies.get(SESSION_COOKIE), None)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response
