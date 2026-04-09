from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScheduleTask(BaseModel):
    job_id: str
    target_type: str
    target_id: Optional[int] = None
    cron: str
    new_name: Optional[str] = None
    image_path: Optional[str] = None
    create_time: datetime = Field(default_factory=datetime.now)