<div align="center">

# nonebot-plugin-avatar-manager

_✨ NoneBot2 头像管理插件，支持机器人与群资料的立即修改和定时修改 ✨_

<p>
	<a href="https://pypi.org/project/nonebot-plugin-avatar-manager/">
		<img src="https://img.shields.io/pypi/v/nonebot-plugin-avatar-manager.svg" alt="pypi">
	</a>
	<img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">
	<a href="./LICENSE">
		<img src="https://img.shields.io/badge/license-MIT-green.svg" alt="license">
	</a>
</p>

</div>

## 介绍

nonebot-plugin-avatar-manager 是一个基于 NoneBot2 和 OneBot V11 的头像管理插件，用于修改机器人自身头像与昵称，以及群头像与群名称，并支持定时任务持久化恢复。

当前源码已经调整为模板仓库常见布局：

```text
.
├── .github/
├── src/
│   └── nonebot_plugin_avatar_manager/
├── tests/
├── LICENSE
├── pyproject.toml
└── README.md
```

## 功能

- 私聊查看机器人当前信息与所在群列表
- 私聊查看机器人在哪些群具有管理权限
- 群聊中由群主或管理员立即修改当前群头像或群名称
- 超级管理员立即修改机器人自身头像或昵称
- 为当前群或机器人自身创建定时修改任务
- 查看、删除已保存任务
- 启动时自动恢复 data/avatar_manager/tasks.json 中的任务

## 安装方法

<details open>
<summary>使用 nb-cli 安装</summary>

在 NoneBot2 项目的根目录执行：

```bash
nb plugin install nonebot-plugin-avatar-manager
```

</details>

<details>
<summary>使用包管理器安装</summary>

```bash
pip install nonebot-plugin-avatar-manager
```

</details>

安装后，在你的 NoneBot2 项目配置中启用插件：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_avatar_manager"]
```

## 配置

建议至少在 `.env` 中配置超级管理员：

```env
SUPERUSERS=["123456789"]
```

插件内部配置项如下：

```python
class Config(BaseModel):
		superusers: list[str] = Field(default_factory=list)
		enable_self_avatar: bool = True
		enable_group_avatar: bool = True
```

## 使用说明

权限约束：

- 私聊中仅超级管理员可查看机器人信息和管理机器人自身资料
- 群聊中仅群主或管理员可管理当前群资料

主要命令：

```text
头像帮助
头像信息
群管
修改 <图片或名称>
定时修改 <cron> <图片或名称>
bot修改 <图片或名称>
bot定时修改 <cron> <图片或名称>
定时列表
删除定时 <job_id>
```

示例：

```text
修改 https://example.com/avatar.jpg
修改 新群名
修改 https://example.com/avatar.jpg 新群名
定时修改 0 8 * * * https://example.com/avatar.jpg
bot修改 https://example.com/avatar.jpg 新昵称
bot定时修改 0 9 * * 1 https://example.com/avatar.jpg
```

常见 cron 示例：

```text
0 8 * * *    每天 8 点
0 9 * * 1    每周一 9 点
*/30 * * * * 每 30 分钟执行一次
```

## 数据文件

- data/avatar_manager/tasks.json：保存定时任务
- data/avatar_manager/temp：保存下载的临时图片

## 适配说明

插件当前通过 OneBot V11 风格 API 尝试执行以下操作：

- set_qq_avatar
- set_qq_profile
- set_group_portrait
- set_group_name

这些接口是否可用，取决于你接入的具体 OneBot V11 实现。若目标实现不支持对应 API，插件会返回失败信息并记录日志。

## 开发

本仓库已调整为可直接打包发布的插件仓库，使用 PDM 作为构建后端。

```bash
pdm install
```

```bash
pdm run lint
```

```bash
pdm run test
```

发布时创建 `v*` 标签即可触发 `.github/workflows/release.yml`。
