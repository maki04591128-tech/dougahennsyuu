from fastapi import APIRouter, Depends, HTTPException

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.provider import (
    DiscoveryResult,
    ProviderConfig,
    ProviderStatus,
    RegisterProviderRequest,
)

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=list[ProviderStatus])
async def list_providers(c: AppContainer = Depends(get_container)):
    return await c.providers.statuses()


@router.post("/discover", response_model=DiscoveryResult)
async def discover(c: AppContainer = Depends(get_container)):
    return await c.providers.discover()


@router.post("/register", response_model=ProviderConfig, status_code=201)
async def register(body: RegisterProviderRequest, c: AppContainer = Depends(get_container)):
    return await c.providers.register(body)


@router.delete("/{provider_id}", status_code=204)
async def delete(provider_id: str, c: AppContainer = Depends(get_container)) -> None:
    try:
        ok = await c.providers.delete(provider_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "provider not found")
