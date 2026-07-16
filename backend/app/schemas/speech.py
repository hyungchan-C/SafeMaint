from pydantic import BaseModel, Field


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speed: float = Field(default=1.0, ge=0.7, le=1.3)
