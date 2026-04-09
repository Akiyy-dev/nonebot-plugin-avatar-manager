import asyncio
import base64
import tempfile
from pathlib import Path

import httpx
from nonebot import logger

TEMP_DIR = Path("data/avatar_manager/temp")


async def download_image(url: str) -> Path | None:
    """下载图片并保存到 data/temp 目录，失败时返回 None。"""
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(url.split("?", maxsplit=1)[0]).suffix or ".jpg"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        def _write_file() -> Path:
            with tempfile.NamedTemporaryFile(
                dir=TEMP_DIR,
                prefix="avatar_",
                suffix=suffix,
                delete=False,
            ) as temp_file:
                temp_file.write(response.content)
                return Path(temp_file.name)

        return await asyncio.to_thread(_write_file)
    except httpx.HTTPError as exception:
        logger.error(f"下载图片失败: {exception}")
    except OSError as exception:
        logger.error(f"写入图片文件失败: {exception}")

    return None


async def image_to_base64(image_path: Path) -> str:
    image_bytes = await asyncio.to_thread(image_path.read_bytes)
    return base64.b64encode(image_bytes).decode("utf-8")
