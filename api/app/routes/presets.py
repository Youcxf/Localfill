from fastapi import APIRouter, Depends, HTTPException, Path, Response

from app.deps import get_ctx, require_login
from app.models import Preset, PresetConfig

router = APIRouter(prefix="/presets", tags=["presets"], dependencies=[Depends(require_login)])

# same rule as the name in the model, so a preset saved here can always be loaded again
PresetName = Path(min_length=1, max_length=50, pattern=r"^[\w .-]+$")


# bracket presets, alerts pick one with "preset": "<name>"
@router.get("", response_model=list[Preset])
async def list_presets(ctx=Depends(get_ctx)):
    return await ctx.brackets.presets()


# creates the preset, or replaces the one with this name
@router.put("/{name}", response_model=Preset)
async def save_preset(config: PresetConfig, name: str = PresetName, ctx=Depends(get_ctx)):
    preset = await ctx.brackets.save_preset(name, config)
    ctx.events.publish("presets")
    return preset


@router.delete("/{name}", status_code=204)
async def delete_preset(name: str, ctx=Depends(get_ctx)):
    if not await ctx.brackets.delete_preset(name):
        raise HTTPException(status_code=404, detail="no such preset")
    ctx.events.publish("presets")
    return Response(status_code=204)
