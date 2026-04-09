import shlex
from datetime import datetime
from pathlib import Path

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .config import Config
from .models import ScheduleTask
from .scheduler import add_job, remove_job, run_task_now, tasks
from .utils import download_image

driver = get_driver()
plugin_config = Config.model_validate(driver.config.dict())
manage_permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER


async def _private_only(event: PrivateMessageEvent) -> bool:
    return True


async def _group_only(event: GroupMessageEvent) -> bool:
    return True


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


avatar_help = on_command(
    "头像帮助",
    aliases={"avatar_help"},
    permission=manage_permission,
    priority=5,
    block=True,
)

avatar_info = on_command(
    "头像信息",
    aliases={"avatar_info"},
    permission=SUPERUSER,
    rule=Rule(_private_only),
    priority=5,
    block=True,
)

group_manage = on_command(
    "群管",
    permission=SUPERUSER,
    rule=Rule(_private_only),
    priority=5,
    block=True,
)

group_modify = on_command(
    "修改",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

group_schedule = on_command(
    "定时修改",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

bot_modify = on_command(
    "bot修改",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

bot_schedule = on_command(
    "bot定时修改",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

schedule_list = on_command(
    "定时列表",
    aliases={"schedule_list"},
    permission=manage_permission,
    priority=5,
    block=True,
)

del_schedule = on_command(
    "删除定时",
    aliases={"del_schedule"},
    permission=manage_permission,
    priority=5,
    block=True,
)


def _extract_image_input(arg: Message) -> str | None:
    for segment in arg:
        if segment.type != "image":
            continue

        image_url = segment.data.get("url")
        if image_url:
            return str(image_url)

        image_file = segment.data.get("file")
        if image_file and Path(str(image_file)).exists():
            return str(image_file)

    return None


def _build_job_id(target_type: str) -> str:
    return f"avatar_{target_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}"


async def _resolve_image_value(image_input: str | None) -> str | None:
    if image_input is None:
        return None

    if image_input.startswith(("http://", "https://")):
        downloaded_path = await download_image(image_input)
        if downloaded_path is None:
            raise ValueError("图片下载失败")
        return str(downloaded_path)

    return image_input


async def _parse_modify_payload(arg: Message) -> tuple[str | None, str | None]:
    plain_text = arg.extract_plain_text().strip()
    parts = shlex.split(plain_text) if plain_text else []

    image_input = _extract_image_input(arg)
    if image_input is None and parts and _looks_like_url(parts[0]):
        image_input = parts.pop(0)

    image_path_value = await _resolve_image_value(image_input)
    new_name = " ".join(parts).strip() or None
    if image_path_value is None and new_name is None:
        raise ValueError("至少提供头像图片或新名称之一")

    return image_path_value, new_name


async def _parse_timed_modify_payload(arg: Message) -> tuple[str, str | None, str | None]:
    plain_text = arg.extract_plain_text().strip()
    if not plain_text:
        raise ValueError("参数不能为空")

    parts = shlex.split(plain_text)
    if len(parts) < 5:
        raise ValueError("cron 格式错误，需要 5 段表达式")

    cron = " ".join(parts[:5])
    payload_parts = parts[5:]

    image_input = _extract_image_input(arg)
    if image_input is None and payload_parts and _looks_like_url(payload_parts[0]):
        image_input = payload_parts.pop(0)

    image_path_value = await _resolve_image_value(image_input)
    new_name = " ".join(payload_parts).strip() or None
    if image_path_value is None and new_name is None:
        raise ValueError("至少提供头像图片或新名称之一")

    return cron, image_path_value, new_name


@avatar_help.handle()
async def avatar_help_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    help_text = """
头像管理器

可用命令:
- 头像帮助 / avatar_help
- 头像信息 / avatar_info
- 群管
- 修改
- 定时修改
- bot修改
- bot定时修改
- 定时列表 / schedule_list
- 删除定时 / del_schedule

示例:
- 群聊中发送：修改 https://example.com/avatar.jpg
- 群聊中发送：修改 example
- 群聊中发送：修改 https://example.com/avatar.jpg example
- 群聊中发送：定时修改 0 8 * * * https://example.com/avatar.jpg
- 私聊或群聊中超级管理员发送：bot修改 https://example.com/avatar.jpg
- 私聊或群聊中超级管理员发送：bot定时修改 0 8 * * * https://example.com/avatar.jpg

权限说明:
- 私聊中：仅超级管理员可操作全部目标
- 群聊中：群管理员和群主可配置当前群

注意:
- 具体 API 可用性取决于你使用的 OneBot V11 实现。
""".strip()
    await avatar_help.finish(help_text)


@avatar_info.handle()
async def avatar_info_handler(
    event: PrivateMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        login_info = await bot.get_login_info()
        group_list = await bot.get_group_list()
    except Exception as exception:
        await avatar_info.finish(f"获取头像信息失败: {exception}")

    lines = [
        "头像管理器信息",
        f"机器人 QQ: {bot.self_id}",
        f"机器人昵称: {login_info.get('nickname', '未知')}",
        f"机器人头像: http://q.qlogo.cn/headimg_dl?dst_uin={bot.self_id}&spec=640",
        "所在群列表:",
    ]

    for group in group_list:
        group_id = int(group["group_id"])
        group_name = str(group.get("group_name", "未知群名"))
        lines.append(
            f"- {group_name} ({group_id}) | 群头像: http://p.qlogo.cn/gh/{group_id}/{group_id}/640"
        )

    await avatar_info.finish("\n".join(lines))


@group_manage.handle()
async def group_manage_handler(
    event: PrivateMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        group_list = await bot.get_group_list()
    except Exception as exception:
        await group_manage.finish(f"获取群列表失败: {exception}")

    manageable_groups: list[str] = []
    for group in group_list:
        group_id = int(group["group_id"])
        try:
            member_info = await bot.get_group_member_info(
                group_id=group_id,
                user_id=int(bot.self_id),
            )
        except Exception as exception:
            logger.warning(f"查询群 {group_id} 权限失败: {exception}")
            continue

        role = str(member_info.get("role", "member"))
        if role in {"owner", "admin"}:
            group_name = str(group.get("group_name", "未知群名"))
            manageable_groups.append(f"- {group_id} | {group_name} | {role}")

    if not manageable_groups:
        await group_manage.finish("无管理权限")

    await group_manage.finish("可管理群列表:\n" + "\n".join(manageable_groups))


@group_modify.handle()
async def group_modify_handler(
    event: GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_group_avatar:
            await group_modify.finish("当前未启用群头像/群名称修改功能")

        image_path, new_name = await _parse_modify_payload(arg)
        task = ScheduleTask(
            job_id=_build_job_id("group"),
            target_type="group",
            target_id=int(event.group_id),
            cron="0 0 1 1 *",
            new_name=new_name,
            image_path=image_path,
        )
        success, message = await run_task_now(task)
        if not success:
            await group_modify.finish(f"立即修改失败: {message}")
    except ValueError as exception:
        await group_modify.finish(str(exception))
    except Exception as exception:
        await group_modify.finish(f"立即修改失败: {exception}")

    await group_modify.finish("已立即修改当前群配置")


@group_schedule.handle()
async def group_schedule_handler(
    event: GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_group_avatar:
            await group_schedule.finish("当前未启用群头像/群名称修改功能")

        cron, image_path, new_name = await _parse_timed_modify_payload(arg)
        task = ScheduleTask(
            job_id=_build_job_id("group"),
            target_type="group",
            target_id=int(event.group_id),
            cron=cron,
            new_name=new_name,
            image_path=image_path,
        )
        add_job(task)
    except ValueError as exception:
        await group_schedule.finish(str(exception))
    except Exception as exception:
        await group_schedule.finish(f"添加定时任务失败: {exception}")

    await group_schedule.finish(f"已添加定时任务 ID: {task.job_id}")


@bot_modify.handle()
async def bot_modify_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_self_avatar:
            await bot_modify.finish("当前未启用机器人自身头像/昵称修改功能")

        image_path, new_name = await _parse_modify_payload(arg)
        task = ScheduleTask(
            job_id=_build_job_id("self"),
            target_type="self",
            cron="0 0 1 1 *",
            new_name=new_name,
            image_path=image_path,
        )
        success, message = await run_task_now(task)
        if not success:
            await bot_modify.finish(f"立即修改失败: {message}")
    except ValueError as exception:
        await bot_modify.finish(str(exception))
    except Exception as exception:
        await bot_modify.finish(f"立即修改失败: {exception}")

    await bot_modify.finish("已立即修改机器人配置")


@bot_schedule.handle()
async def bot_schedule_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_self_avatar:
            await bot_schedule.finish("当前未启用机器人自身头像/昵称修改功能")

        cron, image_path, new_name = await _parse_timed_modify_payload(arg)
        task = ScheduleTask(
            job_id=_build_job_id("self"),
            target_type="self",
            cron=cron,
            new_name=new_name,
            image_path=image_path,
        )
        add_job(task)
    except ValueError as exception:
        await bot_schedule.finish(str(exception))
    except Exception as exception:
        await bot_schedule.finish(f"添加定时任务失败: {exception}")

    await bot_schedule.finish(f"已添加定时任务 ID: {task.job_id}")


@schedule_list.handle()
async def schedule_list_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    filtered_tasks = list(tasks.values())
    if isinstance(event, GroupMessageEvent):
        filtered_tasks = [
            task
            for task in filtered_tasks
            if task.target_type == "group" and task.target_id == int(event.group_id)
        ]

    if not filtered_tasks:
        await schedule_list.finish("当前没有定时任务")

    lines = [
        (
            f"- {task.job_id} | target={task.target_type} | target_id={task.target_id or '-'}"
            f" | cron={task.cron} | name={task.new_name or '-'} | image={task.image_path or '-'}"
        )
        for task in filtered_tasks
    ]
    await schedule_list.finish("已保存定时任务:\n" + "\n".join(lines))


@del_schedule.handle()
async def del_schedule_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    job_id = arg.extract_plain_text().strip()
    if not job_id:
        await del_schedule.finish("请提供要删除的任务 ID")

    task = tasks.get(job_id)
    if isinstance(event, GroupMessageEvent):
        if task is None or task.target_type != "group" or task.target_id != int(event.group_id):
            await del_schedule.finish("未找到本群对应的任务 ID")

    if not remove_job(job_id):
        await del_schedule.finish(f"未找到任务 ID: {job_id}")

    await del_schedule.finish(f"已删除定时任务 ID: {job_id}")