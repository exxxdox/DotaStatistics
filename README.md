# DotaStatistics

在 QQ 私聊或群聊中查询 Dota 2 战绩、追踪选手、查看英雄胜率，并用自然语言分析比赛。

- **使用机器人**：从下面的「开始使用」阅读，无需安装程序。
- **自己部署**：阅读「部署机器人」，准备 QQ 机器人凭据和 DeepSeek API Key。

## 开始使用

群聊中先 **@机器人**，再发送命令；私聊直接发送即可。也可以点击机器人菜单中的命令。

看到“当前查询繁忙，请稍后再试”时，本次命令尚未执行，请稍后重新发送；已有对话记录和未完成参数填写保留。

| 功能 | 输入示例 | 返回内容 |
| --- | --- | --- |
| 追踪选手 | `追踪术 小明 123456789` | 将昵称绑定到 Dota ID |
| 近期战绩 | `撒情况 小明` | 最近 5 场天梯比赛的英雄、胜负和关键数据 |
| 每日简报 | `简报` | 已追踪选手近 24 小时的战绩与 AI 点评 |
| 英雄胜率 | `高胜率英雄` | 最近 30 天全分段英雄胜率 Top 10，不使用 AI 分析 |
| 查看群标识 | `查看当前群OpenID` 或 `群OpenID` | 当前群的 OpenID，仅群聊可用 |
| 清空对话 | `/clear` | 清空当前私聊或当前群的对话记录及未完成参数填写，保留选手绑定 |
| AI 查询与聊天 | `查下小明的段位和英雄池` | 对话中查询 OpenDota 玩家、比赛、英雄、战队或公共数据，也可以直接闲聊 |

查询选手前，先用 `追踪术` 绑定昵称和 Dota ID。昵称不能含空格，最多 256 个字符；Dota ID 必须是正整数。再次绑定同一昵称可更新对应的 Dota ID。

选手名单由所有会话共用。同一昵称再次绑定会覆盖原 Dota ID；如果提示没有权限，请联系机器人管理员。

第一次使用，可以依次发送 `追踪术 小明 123456789`、`撒情况 小明`，再问“小明最近表现怎么样？”。请把示例 ID 换成实际的 Dota ID。

### 缺少参数时怎么办？

直接发送 `追踪术` 或 `撒情况`，机器人会逐步询问缺少的参数。

例如，依次发送：

1. `追踪术`
2. `小明`
3. `123456789`

填错参数时，按提示补充即可。发送 `取消` 或 `退出` 可结束填写，也可以发送另一条命令。群聊中，每个人的填写过程独立。

## 用自然语言查询

直接描述想了解的内容，例如：

- “查下小明的段位和英雄池。”
- “小明最近 7 天的 5 场对局，再分析最新一场。”
- “目前记录了哪些 Dota 用户？”

机器人会按需查询 OpenDota，也可以直接闲聊。查询玩家时使用已绑定的昵称；查询指定比赛、英雄或战队时，可提供对应的数字 ID。

支持查询玩家资料、胜负、英雄池、队友、段位与排名，以及单场比赛摘要、英雄克制、战队资料和职业、直播、公开比赛摘要。

使用时注意：

- 自然语言查询的对局明细不限天梯，并包含比赛 ID，可继续追问单场表现。
- 玩家时间范围支持 1 至 365 天，仅对对局明细、胜负、英雄池、队友、总计和分类统计生效。
- 战队和联赛目录每次最多返回 20 条，只覆盖上游列表的一部分；不支持按名称搜索，也不保证完整。
- AI 查询只读；修改选手绑定请使用 `追踪术`。不支持任意网址查询、SQL、OpenDota 写操作、搜索或解析任务。

## 英雄胜率榜

榜单来自 OpenDota 公开比赛样本，统计最近 30 个完整 UTC 自然日，展示至少出场 100 次的英雄胜率前 10 名。

首次查询可能需要等待。看到“英雄胜率数据正在更新”时，稍后再次发送 `高胜率英雄` 即可。数据源暂不可用时，机器人可能返回最近成功的缓存榜单，并标明缓存状态。

## 对话记录与隐私

- 用户消息和机器人回复会压缩保存，重启后仍保留。
- 每位私聊用户最多保存 100 MB，每个群最多保存 200 MB；达到上限后删除最旧记录。
- 所有会话数据库的总预算约为 2 GB；超出后按最后活动时间淘汰最旧的会话。
- 私聊用户之间、不同群之间的聊天记录独立；同一群共用群内对话历史。
- AI 每次只参考最近一部分对话，不保证记起所有旧消息。
- 已绑定的选手资料长期保留，不随聊天记录淘汰。追踪名单在机器人内共用，可询问“目前记录了哪些 Dota 用户？”查看。
- AI 只可调用固定的只读查询工具，不能在对话中新增或修改选手绑定。

机器人只能保存它实际收到的消息，无法记录未收到的群消息。

## 部署机器人

以下命令均在项目根目录执行。推荐 Docker Compose；本地运行需要 Python 3.12 和 uv，Linux 服务器也可使用 systemd。

### 1. 准备配置

准备可用的 QQ 机器人 App ID、App Secret，以及 DeepSeek API Key。机器人需要能够访问 QQ、OpenDota 和 DeepSeek 服务。

先复制配置模板；如果已有 `.env`，直接编辑，避免覆盖现有配置。

Linux / macOS：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

用文本编辑器打开 `.env`，填写以下配置。不要上传或公开这个文件。

| 配置项 | 必填 | 说明 |
| --- | --- | --- |
| `QQBOT_APP_ID` | 是 | QQ 机器人 App ID |
| `QQBOT_APP_SECRET` | 是 | QQ 机器人 App Secret |
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key，用于 AI 聊天和点评 |
| `DOTA_OPERATOR_IDS` | 否 | 允许执行 `追踪术` 的 QQ 用户 OpenID，多个值用逗号分隔；留空允许所有人修改共用名单 |
| `OPENDOTA_API_KEY` | 否 | OpenDota API Key，可提高 API 调用限额 |
| `OPENDOTA_PROXY` | 否 | 仅 OpenDota 使用的 HTTP/HTTPS 代理地址，例如 `http://127.0.0.1:7890`；留空保留 Requests 默认环境代理行为 |
| `DOTA_AGENT_MAX_TOOL_CALLS` | 否 | 每次 AI 对话的工具调用上限，默认 `4`；必须为正整数，超过 `8` 按 `8` 处理 |
| `DOTA_COMMAND_WORKERS` | 否 | 同步命令并发上限，默认 `4`，允许 `1–32`；满载立即提示繁忙，修改后重启生效。增大会提高线程占用和同时请求上游的数量 |

QQ HTTP 与 WebSocket 连接强制校验证书和域名。SDK 日志固定为 INFO，`--debug` 不会启用可能包含令牌和消息正文的 SDK 调试日志。

`OPENDOTA_PROXY` 在客户端启动时读取，修改后需重启服务。Docker 内的 `127.0.0.1` 指向容器自身；Docker Desktop 访问宿主机代理时可使用 `http://host.docker.internal:7890`，代理需允许容器访问。此配置不影响 DeepSeek 或 QQ。

注意：群 OpenID 是群标识，不能作为 `DOTA_OPERATOR_IDS` 中的用户 OpenID。

### 2. 使用 Docker Compose 启动

安装并启动 Docker，确认 Docker Compose 可用，然后执行：

```bash
docker compose up -d --build
docker compose logs -f dota
```

启动后，在 QQ 中私聊机器人，或在群聊中 @机器人发送 `撒情况`，检查是否收到参数提示。日志查看可用 `Ctrl+C` 退出，不会停止机器人。

### 日常维护

| 操作 | 命令 |
| --- | --- |
| 查看运行状态 | `docker compose ps` |
| 查看日志 | `docker compose logs -f dota` |
| 修改配置或更新源码后重新部署 | `docker compose up -d --build` |
| 停止并移除容器，保留数据 | `docker compose down` |

运行数据保存在 Docker 的 `dota-data` 命名卷中，挂载到容器 `/data`，重建容器后仍保留。**不要执行 `docker compose down -v`，它会删除数据卷。**

### 本地运行

安装 Python 3.12 和 uv，并将上述配置项设置为当前进程的环境变量，然后执行：

```bash
uv sync --frozen
uv run python main.py
```

本地运行的数据默认保存在项目 `data/` 目录，可通过环境变量 `DATA_DIR` 指定其他目录。本地启动不会自动读取 `.env`；Docker Compose 会加载该文件，systemd 使用下文的密钥文件。

### Linux systemd 部署

先安装固定版本的 `uv`，并把项目放在 `dota` 用户可读取的路径，例如 `/opt/DotaStatistics`。创建密钥文件：

```bash
sudo install -d -m 0700 -o root -g root /etc/dota
sudo install -m 0600 -o root -g root .env /etc/dota/secrets
sudo bash manage.sh init
```

`init` 会创建 `dota` 账户，再把密钥改为 `root:dota`、`0640`。它不会以 root 下载或执行远程安装脚本。运行数据保存在 `/var/lib/dota`。

查看状态和日志：

```bash
sudo systemctl status dota.service
sudo journalctl -u dota.service -f
```

更新源码后，重新同步锁定的生产依赖并部署：

```bash
uv sync --frozen --no-dev
sudo bash manage.sh deploy
```

### 导入已有数据

导入必须在机器人停止时执行，避免复制中的 SQLite 数据被同时读写。启动时会自动识别 `docker compose cp` 创建的 root 文件并修正权限：

```powershell
docker compose down
docker compose build
docker compose create dota
docker compose cp ./data/. dota:/data
docker compose up -d
```

无需手动执行 `chown`。修权完成后，Python 进程仍以 UID/GID `10001:10001` 运行。
