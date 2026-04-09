from pydantic import BaseModel, Field


class Config(BaseModel):
    superusers: list[str] = Field(default_factory=list)
    enable_self_avatar: bool = True
    enable_group_avatar: bool = True
