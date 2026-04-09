import asyncio
import json
from pathlib import Path

import nonebot
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import ActionFailed
from nonebot_plugin_apscheduler import scheduler

from .models import ScheduleTask
from .utils import TEMP_DIR, image_to_base64

tasks: dict[str, ScheduleTask] = {}
data_dir = Path("data/avatar_manager")
tasks_file = data_dir / "tasks.json"


def load_tasks() -> dict[str, ScheduleTask]:
    if not tasks_file.exists():
        return {}

    try:
        raw_data = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exception:
        logger.error(f"读取任务文件失败: {exception}")
        return {}

    loaded_tasks: dict[str, ScheduleTask] = {}
    for job_id, task_data in raw_data.items():
        try:
            loaded_tasks[job_id] = ScheduleTask.model_validate(task_data)
        except Exception as exception:
            logger.error(f"加载任务 {job_id} 失败: {exception}")
    return loaded_tasks


def save_tasks() -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            job_id: task.model_dump(mode="json")
            for job_id, task in tasks.items()
        }
        tasks_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exception:
        logger.error(f"保存任务文件失败: {exception}")


def _cron_to_kwargs(cron: str) -> dict[str, str]:
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError("cron 格式错误，需要 5 段表达式")

    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


async def _resolve_bot() -> Bot | None:
    bot = next(
        (
            candidate
            for candidate in nonebot.get_bots().values()
            if isinstance(candidate, Bot)
        ),
        None,
    )
    if bot is None:
        logger.warning("当前没有可用的 OneBot V11 Bot，任务已跳过")
    return bot


async def change_avatar_job(task: ScheduleTask, bot: Bot) -> tuple[bool, str]:
    try:
        upload_payload: str | None = None
        if task.image_path:
            image_path = Path(task.image_path)
            if image_path.exists():
                base64_str = await image_to_base64(image_path)
                upload_payload = f"base64://{base64_str}"
            else:
                message = f"任务 {task.job_id} 的图片不存在: {task.image_path}"
                logger.warning(message)
                return False, message

        if task.target_type == "self":
            if upload_payload is not None:
                await bot.call_api("set_qq_avatar", file=upload_payload)
            if task.new_name:
                await bot.call_api("set_qq_profile", nickname=task.new_name)
        elif task.target_type == "group" and task.target_id is not None:
            if upload_payload is not None:
                await bot.call_api(
                    "set_group_portrait",
                    group_id=task.target_id,
                    file=upload_payload,
                )
            if task.new_name:
                await bot.call_api(
                    "set_group_name",
                    group_id=task.target_id,
                    group_name=task.new_name,
                )
        else:
            message = f"任务 {task.job_id} 的目标配置无效，已跳过执行"
            logger.warning(message)
            return False, message

        success_message = f"定时任务执行成功: {task.job_id}"
        logger.success(success_message)
        return True, success_message
    except ActionFailed as exception:
        message = f"任务 {task.job_id} 调用 API 失败: {exception}"
        logger.error(message)
        return False, message
    except Exception as exception:
        message = f"任务 {task.job_id} 执行异常: {exception}"
        logger.exception(message)
        return False, message


async def _run_task(job_id: str) -> None:
    task = tasks.get(job_id)
    if task is None:
        logger.warning(f"未找到任务 ID: {job_id}")
        return

    bot = await _resolve_bot()
    if bot is None:
        return

    await change_avatar_job(task, bot)


async def run_task_now(task: ScheduleTask) -> tuple[bool, str]:
    bot = await _resolve_bot()
    if bot is None:
        return False, "当前没有可用的 OneBot V11 Bot"

    return await change_avatar_job(task, bot)


def add_job(task: ScheduleTask) -> None:
    cron_kwargs = _cron_to_kwargs(task.cron)
    tasks[task.job_id] = task
    scheduler.add_job(
        _run_task,
        "cron",
        id=task.job_id,
        args=[task.job_id],
        replace_existing=True,
        **cron_kwargs,
    )
    save_tasks()


def remove_job(job_id: str) -> bool:
    task = tasks.pop(job_id, None)
    if task is None:
        return False

    try:
        scheduler.remove_job(job_id)
    except Exception as exception:
        logger.warning(f"移除调度任务失败: {job_id} | error={exception}")

    save_tasks()
    return True


async def init_scheduler() -> None:
    tasks.clear()
    tasks.update(load_tasks())

    restored_count = 0
    for task in tasks.values():
        try:
            cron_kwargs = _cron_to_kwargs(task.cron)
            scheduler.add_job(
                _run_task,
                "cron",
                id=task.job_id,
                args=[task.job_id],
                replace_existing=True,
                **cron_kwargs,
            )
            restored_count += 1
        except Exception as exception:
            logger.error(f"恢复任务失败: {task.job_id} | error={exception}")

    logger.info(f"头像管理器定时任务已恢复，共 {restored_count} 个任务")


async def cleanup_temp_files() -> None:
    if not TEMP_DIR.exists():
        return

    for path in TEMP_DIR.iterdir():
        if not path.is_file():
            continue

        try:
            await asyncio.to_thread(path.unlink)
        except OSError as exception:
            logger.warning(f"清理临时文件失败: {path} | error={exception}")
