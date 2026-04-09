import asyncio
import base64
from pathlib import Path

TEMP_DIR = Path("data/avatar_manager/temp")


async def image_to_base64(image_path: Path) -> str:
    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    return base64.b64encode(image_bytes).decode("utf-8")
