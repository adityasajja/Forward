"""The daily briefing message."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from forward.api.deps import get_principal

router = APIRouter()


class BriefingIn(BaseModel):
    top_n: int = 8
    avoid_n: int = 5
    min_price: float = 50.0
    min_day_value_lakh: float = 50.0
    min_atr_pct: float = 0.5
    min_bars: int = 60
    universe: str = "all"
    watchlist: list[str] = Field(default_factory=list)
    ranking: str = "vs_high"
    send_enabled: bool = True


@router.get("/briefing/config")
def briefing_config() -> dict[str, Any]:
    from forward.composition import get_briefing_service

    service = get_briefing_service()
    return {
        "config": service.get_config().model_dump(),
        "last_sent": service.last_sent(),
    }


@router.put("/briefing/config", dependencies=[Depends(get_principal)])
def briefing_save(body: BriefingIn) -> dict[str, Any]:
    from forward.composition import get_briefing_service

    return get_briefing_service().save_config(body.model_dump()).model_dump()


@router.post("/briefing/preview", dependencies=[Depends(get_principal)])
def briefing_preview() -> dict[str, Any]:
    from forward.composition import get_briefing_service

    message, stats = get_briefing_service().preview()
    return {"message": message, "stats": stats}


@router.post("/briefing/send", dependencies=[Depends(get_principal)])
def briefing_send() -> dict[str, Any]:
    from forward.composition import get_briefing_service

    _, result = get_briefing_service().send()
    return result
