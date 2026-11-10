from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100)
    material_id: str = Field(min_length=1, max_length=100)
    channel: str = Field(min_length=1, max_length=50)
    requested_slot: datetime
    requirement_type: Literal["hard", "preferred"]
    fault: str | None = None

    @field_validator("requested_slot")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("requested_slot must include a timezone")
        return value


class ModelResponse(BaseModel):
    """The model proposes a summary; it cannot assert storage or approval facts."""

    summary: str = Field(description="Brief Chinese user-facing summary based on observations")
    reason_code: str = Field(description="Short machine-readable reason, or none")
    suggested_slot: datetime | None = Field(default=None, description="A slot observed in tool results, if relevant")

    @field_validator("suggested_slot")
    @classmethod
    def require_suggestion_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("suggested_slot must include a timezone")
        return value
