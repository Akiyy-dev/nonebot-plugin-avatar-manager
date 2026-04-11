import asyncio
import shlex
from datetime import datetime
from pathlib import Path
from secrets import token_hex

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import Arg, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .config import Config
from .models import ScheduleTask
from .resources import (
    classify_source_token,
    delete_local_storage_item,
    get_local_storage_page,
    get_local_storage_summary,
    has_uploaded_avatars,
    has_uploaded_names,
    resolve_avatar_resource,
    resolve_name_resource,
    save_uploaded_image,
    save_uploaded_name,
)
from .scheduler import (
    add_job,
    list_tasks,
    normalize_cron_expression,
    remove_job,
    run_task_now,
    tasks,
)

driver = get_driver()
plugin_config = Config.model_validate(driver.config.dict())
manage_permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
IMMEDIATE_TASK_CRON = "0 0 1 1 *"
GROUP_MEMBER_QUERY_CONCURRENCY = 6


async def _private_only(event: PrivateMessageEvent) -> bool:
    return True


async def _group_only(event: GroupMessageEvent) -> bool:
    return True


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

group_modify_avatar = on_command(
    "修改头像",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

group_modify_name = on_command(
    "修改名称",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

group_schedule_avatar = on_command(
    "定时修改头像",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

group_schedule_name = on_command(
    "定时修改名称",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

bot_modify_avatar = on_command(
    "bot修改头像",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

bot_modify_name = on_command(
    "bot修改名称",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

bot_schedule_avatar = on_command(
    "bot定时修改头像",
    permission=SUPERUSER,
    priority=5,
    block=True,
)

bot_schedule_name = on_command(
    "bot定时修改名称",
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

upload_resource = on_command(
    "上传",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

random_avatar = on_command(
    "随机头像",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

random_name = on_command(
    "随机名称",
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

local_storage_list = on_command(
    "本地存储列表",
    aliases={"存储列表"},
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
    priority=5,
    block=True,
)

delete_local_storage = on_command(
    "删除本地存储项",
    aliases={"删除存储项"},
    permission=GROUP_ADMIN | GROUP_OWNER,
    rule=Rule(_group_only),
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
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"avatar_{target_type}_{timestamp}_{token_hex(2)}"


def _build_task(
    target_type: str,
    *,
    target_id: int | None = None,
    cron: str = IMMEDIATE_TASK_CRON,
    image_path: str | None = None,
    new_name: str | None = None,
) -> ScheduleTask:
    return ScheduleTask(
        job_id=_build_job_id(target_type),
        target_type=target_type,
        target_id=target_id,
        cron=cron,
        image_path=image_path,
        new_name=new_name,
    )


async def _run_immediate_change(
    target_type: str,
    *,
    target_id: int | None = None,
    image_path: str | None = None,
    new_name: str | None = None,
) -> tuple[bool, str]:
    task = _build_task(
        target_type,
        target_id=target_id,
        image_path=image_path,
        new_name=new_name,
    )
    return await run_task_now(task)


def _create_scheduled_change(
    target_type: str,
    *,
    target_id: int | None = None,
    cron: str,
    image_path: str | None = None,
    new_name: str | None = None,
) -> ScheduleTask:
    task = _build_task(
        target_type,
        target_id=target_id,
        cron=cron,
        image_path=image_path,
        new_name=new_name,
    )
    add_job(task)
    return task


async def _get_manageable_group_entry(
    bot: Bot,
    group: dict,
    semaphore: asyncio.Semaphore,
) -> str | None:
    group_id = int(group["group_id"])
    async with semaphore:
        try:
            member_info = await bot.get_group_member_info(
                group_id=group_id,
                user_id=int(bot.self_id),
            )
        except Exception as exception:
            logger.warning(f"查询群 {group_id} 权限失败: {exception}")
            return None

    role = str(member_info.get("role", "member"))
    if role not in {"owner", "admin"}:
        return None

    group_name = str(group.get("group_name", "未知群名"))
    return f"- {group_id} | {group_name} | {role}"


async def _resolve_image_value(image_input: str | None) -> str | None:
    if image_input is None:
        return None

    if image_input.startswith(("http://", "https://")):
        return image_input

    local_path = Path(image_input)
    if local_path.exists():
        return str(local_path)

    raise ValueError("图片资源不存在")


async def _parse_avatar_payload(arg: Message) -> str | None:
    plain_text = arg.extract_plain_text().strip()
    parts = shlex.split(plain_text) if plain_text else []
    image_input = _extract_image_input(arg)
    if image_input is not None:
        if parts:
            raise ValueError("修改头像命令仅支持一个图片、目录或图片清单参数")
        return await _resolve_image_value(image_input)

    if not parts:
        return None
    if len(parts) != 1:
        raise ValueError("修改头像命令仅支持一个图片、目录或图片清单参数")

    token_type = await classify_source_token(parts[0])
    if token_type not in {"avatar", "avatar_collection", "avatar_manifest"}:
        raise ValueError("请提供图片、目录或图片清单作为头像来源")

    return await _resolve_image_value(parts[0])


async def _parse_name_payload(arg: Message) -> str | None:
    if _extract_image_input(arg) is not None:
        raise ValueError("修改名称命令不支持图片消息")

    plain_text = arg.extract_plain_text().strip()
    if not plain_text:
        return None

    parts = shlex.split(plain_text)
    if len(parts) == 1:
        token_type = await classify_source_token(parts[0])
        if token_type == "name_manifest":
            return parts[0]
        if token_type in {"avatar", "avatar_collection", "avatar_manifest"}:
            raise ValueError("请提供名称文本或名称清单作为名称来源")

    return plain_text


def _split_timed_command_parts(parts: list[str]) -> tuple[str, list[str]]:
    candidate_lengths = [5]
    if len(parts) >= 6:
        candidate_lengths = [6, 5] if "?" in parts[:6] else [5, 6]

    for field_count in candidate_lengths:
        if len(parts) < field_count:
            continue

        try:
            cron = normalize_cron_expression(" ".join(parts[:field_count]))
        except ValueError:
            continue

        return cron, parts[field_count:]

    raise ValueError("cron 格式错误，需要 5 或 6 段表达式")


async def _parse_timed_avatar_payload(arg: Message) -> tuple[str, str | None]:
    plain_text = arg.extract_plain_text().strip()
    if not plain_text:
        raise ValueError("参数不能为空")

    parts = shlex.split(plain_text)
    if len(parts) < 5:
        raise ValueError("cron 格式错误，需要 5 或 6 段表达式")

    cron, payload_parts = _split_timed_command_parts(parts)

    image_input = _extract_image_input(arg)
    if image_input is not None:
        return cron, await _resolve_image_value(image_input)

    payload_text = " ".join(payload_parts).strip()
    if not payload_text:
        return cron, None
    if len(payload_parts) != 1:
        raise ValueError("定时修改头像命令仅支持一个图片、目录或图片清单参数")

    token_type = await classify_source_token(payload_parts[0])
    if token_type not in {"avatar", "avatar_collection", "avatar_manifest"}:
        raise ValueError("请提供图片、目录或图片清单作为头像来源")
    return cron, await _resolve_image_value(payload_parts[0])


async def _parse_timed_name_payload(arg: Message) -> tuple[str, str | None]:
    if _extract_image_input(arg) is not None:
        raise ValueError("定时修改名称命令不支持图片消息")

    plain_text = arg.extract_plain_text().strip()
    if not plain_text:
        raise ValueError("参数不能为空")

    parts = shlex.split(plain_text)
    if len(parts) < 5:
        raise ValueError("cron 格式错误，需要 5 或 6 段表达式")

    cron, payload_parts = _split_timed_command_parts(parts)
    payload_text = " ".join(payload_parts).strip()
    if not payload_text:
        return cron, None

    if len(payload_parts) == 1:
        token_type = await classify_source_token(payload_parts[0])
        if token_type == "name_manifest":
            return cron, payload_parts[0]
        if token_type in {"avatar", "avatar_collection", "avatar_manifest"}:
            raise ValueError("请提供名称文本或名称清单作为名称来源")

    return cron, payload_text


def _ensure_avatar_resource_available(
    target_type: str,
    target_id: int | None,
    image_source: str | None,
) -> None:
    if image_source is not None:
        return

    if has_uploaded_avatars(target_type, target_id):
        return

    raise ValueError("至少提供头像图片，或先使用上传命令保存头像资源")


def _ensure_name_resource_available(
    target_type: str,
    target_id: int | None,
    name_source: str | None,
) -> None:
    if name_source is not None:
        return

    if has_uploaded_names(target_type, target_id):
        return

    raise ValueError("至少提供名称文本，或先使用上传命令保存名称资源")


def _parse_storage_list_args(arg: Message) -> tuple[str | None, int]:
    plain_text = arg.extract_plain_text().strip()
    if not plain_text:
        return None, 1

    parts = shlex.split(plain_text)
    kind_value = parts[0]
    kind_mapping = {
        "头像": "avatar",
        "名称": "name",
        "avatar": "avatar",
        "name": "name",
    }
    kind = kind_mapping.get(kind_value)
    if kind is None:
        raise ValueError("请使用：本地存储列表 [头像|名称] [页码]")

    if len(parts) == 1:
        return kind, 1
    if len(parts) > 2:
        raise ValueError("参数过多，请使用：本地存储列表 [头像|名称] [页码]")

    if not parts[1].isdigit():
        raise ValueError("页码必须为正整数")

    page = int(parts[1])
    if page < 1:
        raise ValueError("页码必须大于等于 1")
    return kind, page


def _parse_storage_delete_args(arg: Message) -> tuple[str, int]:
    plain_text = arg.extract_plain_text().strip()
    if not plain_text:
        raise ValueError("请使用：删除本地存储项 [头像|名称] [序号]")

    parts = shlex.split(plain_text)
    if len(parts) != 2:
        raise ValueError("请使用：删除本地存储项 [头像|名称] [序号]")

    kind_mapping = {
        "头像": "avatar",
        "名称": "name",
        "avatar": "avatar",
        "name": "name",
    }
    kind = kind_mapping.get(parts[0])
    if kind is None:
        raise ValueError("请使用：删除本地存储项 [头像|名称] [序号]")

    if not parts[1].isdigit():
        raise ValueError("序号必须为正整数")

    index = int(parts[1])
    if index < 1:
        raise ValueError("序号必须大于等于 1")
    return kind, index


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
- 修改头像
- 修改名称
- 定时修改头像
- 定时修改名称
- bot修改头像
- bot修改名称
- bot定时修改头像
- bot定时修改名称
- 上传
- 随机头像
- 随机名称
- 本地存储列表
- 删除本地存储项
- 定时列表 / schedule_list
- 删除定时 / del_schedule

示例:
- 群聊中发送：修改头像 https://example.com/avatar.jpg
- 群聊中发送：修改头像 https://example.com/avatar_list.txt
- 群聊中发送：修改名称 新群名
- 群聊中发送：修改名称 name_list.txt
- 群聊中发送：定时修改头像 0 8 * * * https://example.com/avatar_list.txt
- 群聊中发送：定时修改名称 0 8 * * * name_list.txt
- 群聊中发送：上传
- 群聊中发送：取消
- 群聊中发送：随机头像
- 群聊中发送：随机名称
- 群聊中发送：本地存储列表
- 群聊中发送：本地存储列表 头像 2
- 群聊中发送：本地存储列表 名称 1
- 群聊中发送：删除本地存储项 头像 3
- 群聊中发送：删除本地存储项 名称 2
- 私聊或群聊中超级管理员发送：bot修改头像 https://example.com/avatar.jpg
- 私聊或群聊中超级管理员发送：bot修改名称 新昵称
- 私聊或群聊中超级管理员发送：bot定时修改头像 0 8 * * * https://example.com/avatar_list.txt
- 私聊或群聊中超级管理员发送：bot定时修改名称 0 8 * * * name_list.txt

权限说明:
- 私聊中：仅超级管理员可操作全部目标
- 群聊中：群管理员和群主可配置当前群

注意:
- 具体 API 可用性取决于你使用的 OneBot V11 实现。
- 图片清单 txt 与名称清单 txt 会在执行前重新读取，并与本群已上传资源合并。
- 上传图片会保存到 data 中，并写入本群本地存储列表。
- 本地存储列表支持分页查看，适合头像资源较多时逐页检查。
- 删除本地存储项使用列表中显示的全局序号，而不是页内序号。
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

    semaphore = asyncio.Semaphore(GROUP_MEMBER_QUERY_CONCURRENCY)
    manageable_groups = [
        group_entry
        for group_entry in await asyncio.gather(
            *[
                _get_manageable_group_entry(bot, group, semaphore)
                for group in group_list
            ]
        )
        if group_entry is not None
    ]

    if not manageable_groups:
        await group_manage.finish("无管理权限")

    await group_manage.finish("可管理群列表:\n" + "\n".join(manageable_groups))


@group_modify_avatar.handle()
async def group_modify_avatar_handler(
    event: GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_group_avatar:
            await group_modify_avatar.finish("当前未启用群头像修改功能")

        image_path = await _parse_avatar_payload(arg)
        _ensure_avatar_resource_available("group", int(event.group_id), image_path)
        success, message = await _run_immediate_change(
            "group",
            target_id=int(event.group_id),
            image_path=image_path,
        )
        if not success:
            await group_modify_avatar.finish(f"立即修改头像失败: {message}")
    except ValueError as exception:
        await group_modify_avatar.finish(str(exception))
    except Exception as exception:
        await group_modify_avatar.finish(f"立即修改头像失败: {exception}")

    await group_modify_avatar.finish("已立即修改当前群头像")


@group_modify_name.handle()
async def group_modify_name_handler(
    event: GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_group_avatar:
            await group_modify_name.finish("当前未启用群名称修改功能")

        new_name = await _parse_name_payload(arg)
        _ensure_name_resource_available("group", int(event.group_id), new_name)
        success, message = await _run_immediate_change(
            "group",
            target_id=int(event.group_id),
            new_name=new_name,
        )
        if not success:
            await group_modify_name.finish(f"立即修改名称失败: {message}")
    except ValueError as exception:
        await group_modify_name.finish(str(exception))
    except Exception as exception:
        await group_modify_name.finish(f"立即修改名称失败: {exception}")

    await group_modify_name.finish("已立即修改当前群名称")


@group_schedule_avatar.handle()
async def group_schedule_avatar_handler(
    event: GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_group_avatar:
            await group_schedule_avatar.finish("当前未启用群头像修改功能")

        cron, image_path = await _parse_timed_avatar_payload(arg)
        _ensure_avatar_resource_available("group", int(event.group_id), image_path)
        task = _create_scheduled_change(
            "group",
            target_id=int(event.group_id),
            cron=cron,
            image_path=image_path,
        )
    except ValueError as exception:
        await group_schedule_avatar.finish(str(exception))
    except Exception as exception:
        await group_schedule_avatar.finish(f"添加头像定时任务失败: {exception}")

    await group_schedule_avatar.finish(f"已添加头像定时任务 ID: {task.job_id}")


@group_schedule_name.handle()
async def group_schedule_name_handler(
    event: GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_group_avatar:
            await group_schedule_name.finish("当前未启用群名称修改功能")

        cron, new_name = await _parse_timed_name_payload(arg)
        _ensure_name_resource_available("group", int(event.group_id), new_name)
        task = _create_scheduled_change(
            "group",
            target_id=int(event.group_id),
            cron=cron,
            new_name=new_name,
        )
    except ValueError as exception:
        await group_schedule_name.finish(str(exception))
    except Exception as exception:
        await group_schedule_name.finish(f"添加名称定时任务失败: {exception}")

    await group_schedule_name.finish(f"已添加名称定时任务 ID: {task.job_id}")


@bot_modify_avatar.handle()
async def bot_modify_avatar_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_self_avatar:
            await bot_modify_avatar.finish("当前未启用机器人自身头像修改功能")

        image_path = await _parse_avatar_payload(arg)
        _ensure_avatar_resource_available("self", None, image_path)
        success, message = await _run_immediate_change(
            "self",
            image_path=image_path,
        )
        if not success:
            await bot_modify_avatar.finish(f"立即修改头像失败: {message}")
    except ValueError as exception:
        await bot_modify_avatar.finish(str(exception))
    except Exception as exception:
        await bot_modify_avatar.finish(f"立即修改头像失败: {exception}")

    await bot_modify_avatar.finish("已立即修改机器人头像")


@bot_modify_name.handle()
async def bot_modify_name_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_self_avatar:
            await bot_modify_name.finish("当前未启用机器人自身名称修改功能")

        new_name = await _parse_name_payload(arg)
        _ensure_name_resource_available("self", None, new_name)
        success, message = await _run_immediate_change(
            "self",
            new_name=new_name,
        )
        if not success:
            await bot_modify_name.finish(f"立即修改名称失败: {message}")
    except ValueError as exception:
        await bot_modify_name.finish(str(exception))
    except Exception as exception:
        await bot_modify_name.finish(f"立即修改名称失败: {exception}")

    await bot_modify_name.finish("已立即修改机器人名称")


@bot_schedule_avatar.handle()
async def bot_schedule_avatar_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_self_avatar:
            await bot_schedule_avatar.finish("当前未启用机器人自身头像修改功能")

        cron, image_path = await _parse_timed_avatar_payload(arg)
        _ensure_avatar_resource_available("self", None, image_path)
        task = _create_scheduled_change(
            "self",
            cron=cron,
            image_path=image_path,
        )
    except ValueError as exception:
        await bot_schedule_avatar.finish(str(exception))
    except Exception as exception:
        await bot_schedule_avatar.finish(f"添加头像定时任务失败: {exception}")

    await bot_schedule_avatar.finish(f"已添加头像定时任务 ID: {task.job_id}")


@bot_schedule_name.handle()
async def bot_schedule_name_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    try:
        if not plugin_config.enable_self_avatar:
            await bot_schedule_name.finish("当前未启用机器人自身名称修改功能")

        cron, new_name = await _parse_timed_name_payload(arg)
        _ensure_name_resource_available("self", None, new_name)
        task = _create_scheduled_change(
            "self",
            cron=cron,
            new_name=new_name,
        )
    except ValueError as exception:
        await bot_schedule_name.finish(str(exception))
    except Exception as exception:
        await bot_schedule_name.finish(f"添加名称定时任务失败: {exception}")

    await bot_schedule_name.finish(f"已添加名称定时任务 ID: {task.job_id}")


@schedule_list.handle()
async def schedule_list_handler(
    event: PrivateMessageEvent | GroupMessageEvent, bot: Bot, arg=CommandArg()
) -> None:
    filtered_tasks = (
        list_tasks(target_type="group", target_id=int(event.group_id))
        if isinstance(event, GroupMessageEvent)
        else list_tasks()
    )

    if not filtered_tasks:
        await schedule_list.finish("当前没有定时任务")

    lines = [
        " | ".join(
            [
                f"- {task.job_id}",
                f"target={task.target_type}",
                f"target_id={task.target_id or '-'}",
                f"cron={task.cron}",
                f"name={task.new_name or '-'}",
                f"image={task.image_path or '-'}",
            ]
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
        invalid_group_task = (
            task is None
            or task.target_type != "group"
            or task.target_id != int(event.group_id)
        )
        if invalid_group_task:
            await del_schedule.finish("未找到本群对应的任务 ID")

    if not remove_job(job_id):
        await del_schedule.finish(f"未找到任务 ID: {job_id}")

    await del_schedule.finish(f"已删除定时任务 ID: {job_id}")


@upload_resource.handle()
async def upload_resource_handler(event: GroupMessageEvent) -> None:
    await upload_resource.send(
        "请发送下一条消息：图片会保存为头像资源，纯文本会保存为名称资源，发送取消可终止上传"
    )


@upload_resource.got("resource", prompt="请发送图片或文本消息")
async def upload_resource_receive_handler(
    event: GroupMessageEvent,
    resource: Message = Arg("resource"),
) -> None:
    image_input = _extract_image_input(resource)
    text = resource.extract_plain_text().strip()
    if image_input is None and text == "取消":
        await upload_resource.finish("已取消上传")

    if image_input is not None:
        try:
            saved_path = await save_uploaded_image(
                "group",
                int(event.group_id),
                image_input,
            )
        except ValueError as exception:
            await upload_resource.finish(str(exception))

        await upload_resource.finish(f"已保存头像资源: {saved_path.name}")

    if not text:
        await upload_resource.reject("未识别到图片或文本，请重新发送")

    try:
        is_new_name = save_uploaded_name("group", int(event.group_id), text)
    except ValueError as exception:
        await upload_resource.finish(str(exception))

    if is_new_name:
        await upload_resource.finish(f"已保存名称资源: {text}")

    await upload_resource.finish(f"名称资源已存在: {text}")


@random_avatar.handle()
async def random_avatar_handler(event: GroupMessageEvent, bot: Bot) -> None:
    image_path = await resolve_avatar_resource(
        None,
        "group",
        int(event.group_id),
        False,
    )
    if image_path is None:
        await random_avatar.finish("当前本地存储列表中没有可用头像资源")

    task = ScheduleTask(
        job_id=_build_job_id("group"),
        target_type="group",
        target_id=int(event.group_id),
        cron=IMMEDIATE_TASK_CRON,
        image_path=image_path,
    )
    success, message = await run_task_now(task)
    if not success:
        await random_avatar.finish(f"随机更换头像失败: {message}")

    await random_avatar.finish("已从本地存储列表中随机更换当前群头像")


@random_name.handle()
async def random_name_handler(event: GroupMessageEvent, bot: Bot) -> None:
    name_value = await resolve_name_resource(
        None,
        "group",
        int(event.group_id),
        False,
    )
    if name_value is None:
        await random_name.finish("当前本地存储列表中没有可用名称资源")

    task = ScheduleTask(
        job_id=_build_job_id("group"),
        target_type="group",
        target_id=int(event.group_id),
        cron=IMMEDIATE_TASK_CRON,
        new_name=name_value,
    )
    success, message = await run_task_now(task)
    if not success:
        await random_name.finish(f"随机更换名称失败: {message}")

    await random_name.finish("已从本地存储列表中随机更换当前群名称")


@local_storage_list.handle()
async def local_storage_list_handler(
    event: GroupMessageEvent,
    arg=CommandArg(),
) -> None:
    try:
        kind, page = _parse_storage_list_args(arg)
        if kind is None:
            summary = get_local_storage_summary("group", int(event.group_id))
            message = (
                "本地存储列表摘要\n"
                f"- 头像资源数量: {summary['avatar_count']}\n"
                f"- 名称资源数量: {summary['name_count']}\n"
                "用法:\n"
                "- 本地存储列表 头像 1\n"
                "- 本地存储列表 名称 1"
            )
            await local_storage_list.finish(message)

        items, total, total_pages, start_index = get_local_storage_page(
            "group",
            int(event.group_id),
            kind,
            page,
        )
    except ValueError as exception:
        await local_storage_list.finish(str(exception))

    if not items:
        title = "头像" if kind == "avatar" else "名称"
        await local_storage_list.finish(f"当前本地存储列表中没有{title}资源")

    title = "头像" if kind == "avatar" else "名称"
    lines = [
        f"本地{title}存储列表 第 {page}/{total_pages} 页，共 {total} 项",
    ]
    for offset, item in enumerate(items, start=1):
        lines.append(f"{start_index + offset}. {item}")

    if page < total_pages:
        lines.append(f"下一页: 本地存储列表 {title} {page + 1}")

    await local_storage_list.finish("\n".join(lines))


@delete_local_storage.handle()
async def delete_local_storage_handler(
    event: GroupMessageEvent,
    arg=CommandArg(),
) -> None:
    try:
        kind, index = _parse_storage_delete_args(arg)
        removed_value = delete_local_storage_item(
            "group",
            int(event.group_id),
            kind,
            index,
        )
    except ValueError as exception:
        await delete_local_storage.finish(str(exception))

    title = "头像" if kind == "avatar" else "名称"
    await delete_local_storage.finish(
        f"已删除本地{title}存储项 #{index}: {removed_value}"
    )
