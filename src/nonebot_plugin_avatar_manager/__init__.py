# nonebot2
# nonebot-adapter-onebot
# nonebot-plugin-apscheduler

from nonebot import get_driver, logger, require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")

from . import handlers  # noqa: E402,F401
from . import scheduler  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="头像管理器",
    description="支持定时修改机器人自身头像/昵称以及群头像/群名称（基于 OneBot V11）",
    usage="发送 头像帮助 查看详细指令",
    type="application",
    supported_adapters={"~onebot.v11"},
)

driver = get_driver()


@driver.on_startup
async def _on_startup() -> None:
    await scheduler.init_scheduler()


@driver.on_shutdown
async def _on_shutdown() -> None:
    await scheduler.cleanup_temp_files()


__all__ = ["__plugin_meta__", "handlers", "scheduler"]

logger.success("头像管理器插件加载完成")