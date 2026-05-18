from typing import Any, Literal

from pydantic import BaseModel, Field


class JobMatch(BaseModel):
    job_id: int
    title: str
    company: str
    score: int = Field(ge=0, le=100)
    reasoning: str
    highlight_bullet: str


class ShortlistItem(BaseModel):
    job_id: int
    title: str
    company: str
    distance: float


class SSEEvent(BaseModel):
    type: Literal["shortlist", "match", "done", "error"]
    data: Any
