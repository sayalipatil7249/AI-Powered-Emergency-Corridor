"""
The "Ask" chat: questions about the live simulation, answered by Claude
from the current state.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services import assistant_service


router = APIRouter(
    prefix="/assistant",
    tags=["AI chat"],
)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[Turn] = []


@router.post("/ask")
async def ask(request: Question):
    try:
        answer = await assistant_service.ask(
            request.question,
            [turn.model_dump() for turn in request.history],
        )
    except assistant_service.AssistantUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"answer": answer}


class EmergencyRequest(BaseModel):
    text: str = Field(min_length=3, max_length=600)


@router.post("/intake")
async def intake(request: EmergencyRequest):
    """The operator describes an emergency in their own words; the AI
    fills in the trip planner (place, condition, hospital). The operator
    checks it and starts the trip; the AI never controls signals."""
    try:
        return await assistant_service.intake(request.text)
    except assistant_service.AssistantUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except assistant_service.IntakeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
