## 运行

项目使用 `uv` 管理 Python 3.12、依赖锁文件和 `.venv` 虚拟环境。

开发环境首次安装：

```bash
uv sync
```

运行机器人：

```bash
uv run python main.py
```

Linux 服务器首次部署可执行 `sudo bash ./init.sh`，安装 `uv`、同步生产依赖并配置 systemd。
依赖已经安装时，执行 `sudo bash ./deploy.sh` 即可重新渲染、复制并重启 systemd 服务。部署脚本根据自身真实路径自动计算 `PROJECT_DIR`，不依赖调用时的工作目录。
`startup.sh` 也可用于前台启动；脚本会直接使用项目的 `.venv`，不需要手动激活。

## 每周英雄胜率榜

机器人在线时会在每天 20:00（Asia/Shanghai）向 `QQBOT_GROUP_OPENID` 指定的群发送上一自然周（周一至周日）的榜单。OpenDota 的近期 Explorer 样本以职业比赛为主，因此榜单统计职业队长模式比赛；1—5 号位按同队 GPM 从高到低划分，每个位置展示至少出场 3 次的胜率前三英雄。

## 配置

systemd 从 `/root/.secrets` 读取环境变量：

```dotenv
# qq bot token
QQBOT_APP_ID=xxx
QQBOT_APP_SECRET=xxx
# 每晚 20:00 接收上周五位置英雄胜率榜的 QQ 群 openid
QQBOT_GROUP_OPENID=xxx
# deepseek ai token
DEEPSEEK_API_KEY=xxx
# 可选；配置后可提高 OpenDota API 调用限额
OPENDOTA_API_KEY=xxx
```

任何群成员都可以发送 `@机器人 查看当前群OpenID` 读取当前群的 `group_openid`。配置 `QQBOT_GROUP_OPENID` 后，可发送 `@机器人 测试英雄胜率榜` 手动执行一次与每日定时任务完全相同的生成和主动发送流程；榜单仍发送到配置的目标群，当前群只返回测试结果。

用户私聊机器人时，消息会直接交给 DeepSeek 生成回复，并通过原私聊会话返回；私聊发送 `高胜率英雄` 会直接返回上一自然周的五位置英雄胜率榜。机器人会按群和私聊用户隔离五分钟短期上下文，避免不同会话之间串话。
