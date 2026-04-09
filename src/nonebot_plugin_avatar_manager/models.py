from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleTask(BaseModel):
    job_id: str
    target_type: str
    target_id: int | None = None
    cron: str
    new_name: str | None = None
    image_path: str | None = None
    create_time: datetime = Field(default_factory=datetime.now)
