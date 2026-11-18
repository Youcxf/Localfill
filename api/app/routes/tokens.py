from fastapi import APIRouter, Depends, HTTPException, Response

from app.deps import get_ctx, require_session
from app.models import ApiToken, ApiTokenCreated, TokenCreate

# only from the dashboard: a program can not make itself new tokens
router = APIRouter(prefix="/tokens", tags=["api tokens"], dependencies=[Depends(require_session)])


@router.get("", response_model=list[ApiToken])
async def list_tokens(ctx=Depends(get_ctx)):
    return await ctx.tokens.list()


# the answer is the only time the token is shown
@router.post("", response_model=ApiTokenCreated, status_code=201)
async def create_token(body: TokenCreate, ctx=Depends(get_ctx)):
    return await ctx.tokens.create(body.name.strip())


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: int, ctx=Depends(get_ctx)):
    if not await ctx.tokens.revoke(token_id):
        raise HTTPException(status_code=404, detail="no such token")
    return Response(status_code=204)
