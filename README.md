# DotaStatistics

QQ 群聊/私聊机器人：Dota 2 战绩统计与高胜率英雄分析。数据来自 OpenDota，AI 分析与点评使用 DeepSeek。

## 功能特性

- **追踪选手**：`追踪术` 将昵称绑定到 Dota ID，本地持久化。
- **近期比赛**：`撒情况` 查询指定选手最近几场天梯（game_mode 22）比赛的关键数据。
- **今日战绩**：`今儿` 查询指定选手当天胜负场次。
- **每日简报**：`简报` 汇总所有追踪选手近 24 小时数据，交给 DeepSeek 逐人点评。
- **高胜率英雄**：`高胜率英雄` 输出 OpenDota 最近 30 天公开比赛全分段胜率 Top 10，并追加 DeepSeek 1—5 号位推荐。
- **AI 聊天**：在群聊 @机器人 或私聊直接发消息即可与 DeepSeek 对话，按群/私聊用户隔离 5 分钟短期上下文。
- **指令面板同步**：启动后自动配置单聊自定义菜单和群指令面板，已有指令原地更新，不会重复创建。

## 项目结构

```
.
├── main.py                       # 入口：启动 QQ 机器人
├── qq_bot.py                     # QQ 客户端、命令路由、依赖组装
├── data_center.py                # 共享配置与资源路径
├── lib/
│   ├── open_dota_client.py       # OpenDota API 客户端（缓存、重试、超时拆分、比赛查询）
│   ├── deepseek_api.py           # DeepSeek 对话与数据分析
│   ├── player_repository.py      # 追踪选手映射（name_id.json 读写）
│   ├── hero_name_resolver.py     # 英雄 ID 到名称解析（xlsx 中文名）
│   └── utils.py                  # 通用纯函数
├── service/
│   ├── today.py                  # 每日简报（依赖注入）
│   ├── hero_win_rate_report.py   # 英雄胜率榜报表
│   └── qq_command_discovery.py   # 单聊菜单/群指令面板同步
├── res/
│   ├── dota.service              # systemd 单元模板
│   └── hero_name.xlsx            # 英雄中文名映射
├── data/                          # 运行时数据（自动生成，不提交）
├── tests/                        # pytest 测试
├── Dockerfile                    # 生产镜像定义
├── compose.yaml                  # Docker Compose 部署定义
├── init.sh                       # 首次部署：安装 uv、同步依赖、配置 systemd
├── deploy.sh                     # 渲染并重启 systemd 服务
├── startup.sh                    # 前台启动
├── pyproject.toml
└── uv.lock
```

`data/name_id.json` 和 `data/daily_hero_stats_cache.json` 由运行时生成，已被
gitignore，不会提交。设置 `DATA_DIR` 可以把这两个文件写入其他目录；Docker
Compose 默认将宿主机的 `./data` 映射到容器内的 `/data`。

## 快速开始

环境要求：Python 3.12，使用 [uv](https://docs.astral.sh/uv/) 管理依赖和虚拟环境。

```bash
# 安装依赖并创建 .venv
uv sync

# 复制并配置环境变量（见「配置」）
# 从 .env.example 模板开始，填入真实凭据

# 启动机器人
uv run python main.py
```

## 指令说明

| 指令 | 用法 | 说明 |
| --- | --- | --- |
| `追踪术` | `追踪术 昵称 dotaId` | 绑定昵称与 Dota ID |
| `撒情况` | `撒情况 昵称` | 查询选手近期比赛 |
| `今儿` | `今儿 昵称` | 查询选手今日战绩 |
| `简报` | `简报` | 生成今日比赛简报 |
| `高胜率英雄` | `高胜率英雄` | 查询近期英雄胜率榜 |
| `查看当前群OpenID` | `查看当前群OpenID`（别名 `群OpenID`） | 查看当前群 OpenID，仅供查看 |
| AI 聊天 | 群聊 `@机器人 消息` 或私聊直接发消息 | 交给 DeepSeek 生成回复 |

## 英雄胜率榜

用户在群聊或私聊主动发送 `高胜率英雄` 时，机器人会在当前会话直接回复榜单，不进行任何定时或主动发送。榜单统计 OpenDota `public_matches` 公开比赛样本最近 30 个完整 UTC 自然日的数据，展示至少出场 100 次的整体胜率前 10 名英雄，并把更宽的前 40 名候选数据交给 DeepSeek，追加 1—5 号位各一个高胜率英雄推荐。

统计按天查询并增量缓存到 `data/daily_hero_stats_cache.json`：首次运行串行补齐 30 天，之后每天通常只查询新的一天，避免并发重查询挤占 Explorer 资源。SQL 会先利用 `start_time` 筛选比赛再展开英雄；单日查询若仍触发 statement timeout 或 Query read timeout，会自动拆分为更小的时间段并在本地合并。首次回填超过 QQ 回复时限时，机器人先提示数据正在更新，并在后台继续补缓存；用户稍后再次主动查询即可。OpenDota 遇到 429、5xx、522、524 或网络错误时会进行有限指数退避重试；刷新仍失败时仅使用 48 小时内的最后成功完整快照，并在榜单中标注缓存状态。DeepSeek 推荐失败不会影响客观胜率榜发送。

## 配置

程序从进程环境读取配置。Docker Compose 会通过 `env_file` 加载项目根目录的
`.env`；可以从 `.env.example` 复制一份再填写：

```dotenv
# qq bot token
QQBOT_APP_ID=xxx
QQBOT_APP_SECRET=xxx
# deepseek ai token
DEEPSEEK_API_KEY=xxx
# 可选；配置后可提高 OpenDota API 调用限额
OPENDOTA_API_KEY=xxx
```

任何群成员都可以发送 `@机器人 查看当前群OpenID` 读取当前群的 `group_openid`；该标识仅供查看，不再用于英雄榜配置。发送 `@机器人 高胜率英雄` 会在当前群直接获得榜单回复。

用户私聊机器人时，消息会直接交给 DeepSeek 生成回复，并通过原私聊会话返回；私聊发送 `高胜率英雄` 会直接返回最近 30 个完整自然日的全分段英雄胜率 Top 10 与 DeepSeek 1—5 号位推荐。机器人会按群和私聊用户隔离五分钟短期上下文，避免不同会话之间串话。

## 部署

默认使用 Docker Compose 部署。QQ WebSocket 是容器主动连接远端网关，因此
`compose.yaml` 不发布任何端口；服务器只需允许 DNS、HTTPS 和 WSS 出站访问。
服务器需要安装 Docker Engine、Docker Compose v2，并启用 BuildKit。

```bash
# 1. 创建配置文件并填写真实凭据
cp .env.example .env
chmod 600 .env

# 容器使用 UID/GID 10001 运行，需要拥有持久目录的写权限
mkdir -p data
sudo chown -R 10001:10001 data

# 2. 构建镜像并后台启动
docker compose up -d --build

# 3. 查看启动和连接日志
docker compose logs -f bot
```

如果旧版本已有 `res/name_id.json`，首次启动前先将它迁入新目录，避免已有
追踪关系看起来丢失：

```bash
mkdir -p data
cp res/name_id.json data/name_id.json
sudo chown -R 10001:10001 data
docker compose up -d
```

运行数据保存在宿主机项目目录的 `data/` 中，重新构建或删除容器不会丢失。
更新代码后执行 `docker compose up -d --build` 即可重新构建并替换当前容器。停止服务
使用 `docker compose down`，不会删除 `data/` 中的文件。

备份前可先停止容器，确保两个 JSON 文件没有正在写入：

```bash
docker compose stop bot
tar czf dota-statistics-data.tar.gz -C data .
docker compose start bot
```

不使用 Docker 时仍可通过 `uv run python main.py` 启动；运行数据默认写入项目
根目录下的 `data/`。

## 测试

```bash
uv run pytest
```

测试位于 `tests/`，镜像对应源码模块，使用内存替身替代 OpenDota、DeepSeek、QQ SDK 与文件访问，无需凭据或网络。

## 数据来源

OpenDota API 文档：<https://docs.opendota.com>
