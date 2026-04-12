import asyncio
import json
from pathlib import Path

import nonebot
from apscheduler.triggers.cron import CronTrigger
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import ActionFailed
from nonebot_plugin_apscheduler import scheduler

from .models import ScheduleTask
from .resources import resolve_avatar_resource, resolve_name_resource
from .utils import TEMP_DIR, image_to_base64

tasks: dict[str, ScheduleTask] = {}
data_dir = Path("data/avatar_manager")
tasks_file = data_dir / "tasks.json"
CRON_FIELD_LAYOUTS: tuple[tuple[str, ...], ...] = (
    ("second", "minute", "hour", "day", "month", "day_of_week", "year"),
    ("second", "minute", "hour", "day", "month", "day_of_week"),
    ("minute", "hour", "day", "month", "day_of_week", "year"),
    ("minute", "hour", "day", "month", "day_of_week"),
)


def _sorted_task_items(
    source: dict[str, ScheduleTask] | None = None,
) -> list[tuple[str, ScheduleTask]]:
    task_mapping = tasks if source is None else source
    return sorted(
        task_mapping.items(),
        key=lambda item: (
            item[1].create_time,
            item[1].target_type,
            item[1].target_id or 0,
            item[0],
        ),
    )


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
            for job_id, task in _sorted_task_items()
        }
        tasks_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exception:
        logger.error(f"保存任务文件失败: {exception}")


def _normalize_cron_parts(parts: list[str]) -> list[str]:
    return ["*" if part == "?" else part for part in parts]


def _canonicalize_cron_parts(
    fields: tuple[str, ...],
    parts: list[str],
) -> list[str]:
    if fields == ("minute", "hour", "day", "month", "day_of_week", "year"):
        return ["0", *parts]
    return parts


def _build_cron_kwargs(
    fields: tuple[str, ...],
    parts: list[str],
) -> dict[str, str]:
    return dict(zip(fields, parts, strict=True))


def iter_valid_cron_prefixes(parts: list[str]) -> list[tuple[int, str]]:
    normalized_parts = _normalize_cron_parts(parts)
    valid_prefixes: list[tuple[int, str]] = []
    seen_expressions: set[str] = set()

    for fields in CRON_FIELD_LAYOUTS:
        field_count = len(fields)
        if len(normalized_parts) < field_count:
            continue

        candidate_parts = normalized_parts[:field_count]
        cron_kwargs = _build_cron_kwargs(fields, candidate_parts)
        try:
            CronTrigger(**cron_kwargs)
        except ValueError:
            continue

        canonical_expression = " ".join(
            _canonicalize_cron_parts(fields, candidate_parts)
        )
        if canonical_expression in seen_expressions:
            continue

        seen_expressions.add(canonical_expression)
        valid_prefixes.append((field_count, canonical_expression))

    return valid_prefixes


def normalize_cron_expression(cron: str) -> str:
    parts = cron.split()
    if len(parts) not in {5, 6, 7}:
        raise ValueError("cron 格式错误，需要 5、6 或 7 段表达式")

    return " ".join(_normalize_cron_parts(parts))


def validate_cron_expression(cron: str) -> str:
    parts = cron.split()
    normalized_cron = normalize_cron_expression(cron)
    for field_count, candidate in iter_valid_cron_prefixes(parts):
        if field_count == len(parts):
            return candidate

    raise ValueError(f"cron 表达式无效: {normalized_cron}")


def _cron_to_kwargs(cron: str) -> dict[str, str]:
    canonical_cron = validate_cron_expression(cron)
    parts = canonical_cron.split()
    if len(parts) == 5:
        fields = ("minute", "hour", "day", "month", "day_of_week")
    elif len(parts) == 6:
        fields = ("second", "minute", "hour", "day", "month", "day_of_week")
    else:
        fields = (
            "second",
            "minute",
            "hour",
            "day",
            "month",
            "day_of_week",
            "year",
        )

    return _build_cron_kwargs(fields, parts)


def _schedule_task(task: ScheduleTask) -> None:
    cron_kwargs = _cron_to_kwargs(task.cron)
    scheduler.add_job(
        _run_task,
        "cron",
        id=task.job_id,
        args=[task.job_id],
        replace_existing=True,
        **cron_kwargs,
    )


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


async def change_avatar_job(
    task: ScheduleTask,
    bot: Bot,
    *,
    scheduled: bool,
) -> tuple[bool, str]:
    try:
        resolved_image_path = await resolve_avatar_resource(
            task.image_path,
            task.target_type,
            task.target_id,
            scheduled,
        )
        resolved_name = await resolve_name_resource(
            task.new_name,
            task.target_type,
            task.target_id,
            scheduled,
        )
        if resolved_image_path is None and resolved_name is None:
            return False, "没有可用的头像或名称资源"

        upload_payload: str | None = None
        if resolved_image_path:
            image_path = Path(resolved_image_path)
            if image_path.exists():
                base64_str = await image_to_base64(image_path)
                upload_payload = f"base64://{base64_str}"
            else:
                message = f"任务 {task.job_id} 的图片不存在: {resolved_image_path}"
                logger.warning(message)
                return False, message

        if task.target_type == "self":
            if upload_payload is not None:
                await bot.call_api("set_qq_avatar", file=upload_payload)
            if resolved_name:
                await bot.call_api("set_qq_profile", nickname=resolved_name)
        elif task.target_type == "group" and task.target_id is not None:
            if upload_payload is not None:
                await bot.call_api(
                    "set_group_portrait",
                    group_id=task.target_id,
                    file=upload_payload,
                )
            if resolved_name:
                await bot.call_api(
                    "set_group_name",
                    group_id=task.target_id,
                    group_name=resolved_name,
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

    await change_avatar_job(task, bot, scheduled=True)


async def run_task_now(task: ScheduleTask) -> tuple[bool, str]:
    bot = await _resolve_bot()
    if bot is None:
        return False, "当前没有可用的 OneBot V11 Bot"

    return await change_avatar_job(task, bot, scheduled=False)


def add_job(task: ScheduleTask) -> None:
    _schedule_task(task)
    tasks[task.job_id] = task
    save_tasks()


def list_tasks(
    *,
    target_type: str | None = None,
    target_id: int | None = None,
) -> list[ScheduleTask]:
    filtered_tasks: list[ScheduleTask] = []
    for _, task in _sorted_task_items():
        if target_type is not None and task.target_type != target_type:
            continue
        if target_id is not None and task.target_id != target_id:
            continue
        filtered_tasks.append(task)
    return filtered_tasks


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
    loaded_tasks = load_tasks()
    tasks.clear()

    restored_count = 0
    failed_job_ids: list[str] = []
    for _, task in _sorted_task_items(loaded_tasks):
        try:
            _schedule_task(task)
            tasks[task.job_id] = task
            restored_count += 1
        except Exception as exception:
            failed_job_ids.append(task.job_id)
            logger.error(f"恢复任务失败: {task.job_id} | error={exception}")

    if failed_job_ids:
        save_tasks()

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
