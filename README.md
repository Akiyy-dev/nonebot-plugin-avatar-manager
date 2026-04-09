<div align="center">
    <a href="https://v2.nonebot.dev/store"><img src="https://github.com/A-kirami/nonebot-plugin-template/blob/resources/nbp_logo.png" width="180" height="180" alt="NoneBotPluginLogo"></a>
    <br>
    <p><img src="https://github.com/A-kirami/nonebot-plugin-template/blob/resources/NoneBotPlugin.svg" width="240" alt="NoneBotPluginText"></p>
</div>

<div align="center">

# nonebot-plugin-avatar-manager

_✨ NoneBot2 头像管理插件，支持机器人和群资料的立即修改与定时修改 ✨_

<a href="./LICENSE">
    <img src="https://img.shields.io/github/license/Akiyy-dev/nonebot-plugin-avatar-manager.svg" alt="license">
</a>
<a href="https://pypi.python.org/pypi/nonebot-plugin-avatar-manager">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-avatar-manager.svg" alt="pypi">
</a>
<img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">

</div>

## 📖 介绍

nonebot-plugin-avatar-manager 是一个基于 NoneBot2 和 OneBot V11 的资料管理插件，提供机器人头像/昵称、群头像/群名称的立即修改和定时修改能力，并支持任务持久化恢复。

插件目前支持的核心场景：

- 超级管理员在私聊中查看机器人信息和可管理群列表
- 群主或管理员在群聊中直接修改当前群头像或群名称
- 超级管理员修改机器人自身头像或昵称
- 为群资料或机器人资料创建定时修改任务
- 从图床或 COS 的图片直链 / txt 清单中轮换头像与名称
- 群聊中上传头像资源或名称资源到本群独立资源池
- 启动时自动恢复已保存任务

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>

在 nonebot2 项目的根目录下打开命令行，输入以下指令即可安装：

```bash
nb plugin install nonebot-plugin-avatar-manager
```

</details>

<details>
<summary>使用包管理器安装</summary>

在 nonebot2 项目的插件目录下打开命令行，根据你使用的包管理器，输入相应的安装命令。

<details>
<summary>pip</summary>

```bash
pip install nonebot-plugin-avatar-manager
```

</details>

安装完成后，打开 nonebot2 项目根目录下的 pyproject.toml 文件，在 `[tool.nonebot]` 部分追加写入：

```toml
plugins = ["nonebot_plugin_avatar_manager"]
```

</details>

## ⚙️ 配置

在 nonebot2 项目的 `.env` 文件中添加下表中的配置项：

| 配置项 | 必填 | 默认值 | 说明 |
|:-----:|:----:|:----:|:----|
| SUPERUSERS | 是 | 无 | NoneBot 超级管理员账号列表，私聊管理机器人资料时必需 |
| ENABLE_SELF_AVATAR | 否 | true | 是否允许修改机器人自身头像与昵称 |
| ENABLE_GROUP_AVATAR | 否 | true | 是否允许修改群头像与群名称 |

示例：

```env
SUPERUSERS=["123456789"]
ENABLE_SELF_AVATAR=true
ENABLE_GROUP_AVATAR=true
```

## 🎉 使用

### 指令表

| 指令 | 权限 | 需要@ | 范围 | 说明 |
|:----|:----|:----:|:----:|:----|
| 头像帮助 | 超级管理员 / 群管理员 / 群主 | 否 | 私聊 / 群聊 | 查看插件帮助 |
| 头像信息 | 超级管理员 | 否 | 私聊 | 查看机器人账号、昵称、头像地址与所在群列表 |
| 群管 | 超级管理员 | 否 | 私聊 | 查看机器人在哪些群具备管理权限 |
| 修改 | 群管理员 / 群主 | 否 | 群聊 | 立即修改当前群头像或群名称 |
| 定时修改 | 群管理员 / 群主 | 否 | 群聊 | 为当前群创建定时修改任务 |
| 上传 | 群管理员 / 群主 | 否 | 群聊 | 接收下一条消息并保存为本群头像资源或名称资源 |
| bot修改 | 超级管理员 | 否 | 私聊 / 群聊 | 立即修改机器人头像或昵称 |
| bot定时修改 | 超级管理员 | 否 | 私聊 / 群聊 | 为机器人自身创建定时修改任务 |
| 定时列表 | 超级管理员 / 群管理员 / 群主 | 否 | 私聊 / 群聊 | 查看任务列表；群聊中只显示当前群任务 |
| 删除定时 | 超级管理员 / 群管理员 / 群主 | 否 | 私聊 / 群聊 | 删除指定任务；群聊中仅可删除当前群任务 |

### 使用示例

```text
修改 https://example.com/avatar.jpg
修改 https://example.com/avatar-list.txt
修改 names.txt
修改 avatars.txt names.txt
修改 新群名
修改 https://example.com/avatar.jpg 新群名
定时修改 0 8 * * * https://example.com/avatar.jpg
定时修改 0 8 * * * https://example.com/avatar-list.txt names.txt
定时修改 0 8 * * * 新群名
上传
bot修改 https://example.com/avatar.jpg 新昵称
bot定时修改 0 9 * * 1 https://example.com/avatar.jpg
删除定时 avatar_group_20260409100000
```

### 清单规则

- 头像清单使用 txt 文件，每行一个图片直链或本地图片路径
- 名称清单使用 txt 文件，每行一个名称
- 定时任务执行时会重新读取 txt 清单，并与当前群已上传资源合并
- 上传资源按群隔离存放，不会被 txt 清单覆盖

### Cron 示例

```text
0 8 * * *    每天 8 点执行
0 9 * * 1    每周一 9 点执行
*/30 * * * * 每 30 分钟执行一次
```

### 任务存储

- `data/avatar_manager/tasks.json`：保存定时任务
- `data/avatar_manager/temp`：保存下载的临时图片