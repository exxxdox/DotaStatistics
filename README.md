# DotaStatistics

QQ Dota 2 机器人，支持选手追踪、近期战绩、每日简报、英雄胜率榜和 AI 聊天。

## Docker 启动

复制环境配置并填写三个必填凭据。Compose 会创建 `dota-data` named volume 并挂载到容器 `/data`：

```bash
cp .env.example .env
${EDITOR:-vi} .env
docker compose up -d --build
```

镜像入口会检查 `/data`。只有目录不可访问或导入文件不属于 UID/GID `10001:10001` 时，入口才会自动修正所有权；随后立即降权运行机器人。`docker compose down` 会保留数据；不要使用 `docker compose down -v`，它会删除 volume。

Windows PowerShell 可直接运行：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

## 导入数据

导入必须在机器人停止时执行，避免复制中的 SQLite 数据被同时读写。启动时会自动识别 `docker compose cp` 创建的 root 文件并修正权限：

```powershell
docker compose down
docker compose build
docker compose create dota
docker compose cp ./data/. dota:/data
docker compose up -d
```

无需手动执行 `chown`。修权完成后，Python 进程仍以 UID/GID `10001:10001` 运行。

## systemd 启动

先安装固定版本的 `uv`，并把项目放在 `dota` 用户可读取的路径，例如 `/opt/DotaStatistics`。创建密钥文件：

```bash
sudo install -d -m 0700 -o root -g root /etc/dota
sudo install -m 0600 -o root -g root .env /etc/dota/secrets
sudo bash manage.sh init
```

`init` 会创建 `dota` 账户，再把密钥改为 `root:dota`、`0640`。它不会以 root 下载或执行远程安装脚本。运行数据保存在 `/var/lib/dota`。

## Tag 发布

推送 Git tag 会自动构建 `linux/amd64` Docker 镜像，并在同名 GitHub Release 中上传 TAR：

```bash
git tag v1.0.0
git push origin v1.0.0
```

下载 Release 中的 TAR，然后直接导入镜像：

```bash
docker load --input dotastatistics-v1.0.0-linux-amd64-docker-image.tar
```

导入后的镜像名称为 `dota:v1.0.0`。

## 怎么用

群聊中先 **@机器人**，再发送命令；私聊直接发送即可。也可以点击机器人菜单中的命令。

| 功能 | 输入示例 | 返回内容 |
| --- | --- | --- |
| 追踪选手 | `追踪术 小明 123456789` | 将昵称绑定到 Dota ID |
| 近期战绩 | `撒情况 小明` | 最近 5 场天梯比赛的英雄、胜负和关键数据 |
| 每日简报 | `简报` | 已追踪选手近 24 小时的战绩与 AI 点评 |
| 英雄胜率 | `高胜率英雄` | 最近 30 天全分段英雄胜率 Top 10，不使用 AI 分析 |
| 查看群标识 | `查看当前群OpenID` 或 `群OpenID` | 当前群的 OpenID，仅群聊可用 |
| 清空对话 | `/clear` | 清空当前私聊或当前群的对话记录及未完成参数填写，保留选手绑定 |
| AI Agent | `查下小明的段位和英雄池` | 对话中查询 OpenDota 玩家、比赛、英雄、战队或公共数据，也可以直接闲聊 |

查询选手前，先用 `追踪术` 绑定昵称和 Dota ID。昵称不能含空格，最多 256 个字符；Dota ID 必须是正整数。再次绑定同一昵称可更新对应的 Dota ID。

## 不知道参数怎么填？

直接发送 `追踪术` 或 `撒情况`，机器人会逐步询问缺少的参数。

例如，依次发送：

1. `追踪术`
2. `小明`
3. `123456789`

填错参数时，按提示补充即可。发送 `取消` 或 `退出` 可结束填写，也可以发送另一条命令。群聊中，每个人的填写过程独立。

## 英雄胜率榜

榜单来自 OpenDota 公开比赛样本，统计最近 30 个完整 UTC 自然日，展示至少出场 100 次的英雄胜率前 10 名。

首次查询可能需要等待。看到“英雄胜率数据正在更新”时，稍后再次发送 `高胜率英雄` 即可。数据源暂不可用时，机器人可能返回最近成功的缓存榜单，并标明缓存状态。

## 对话与选手记忆

- 用户消息和机器人回复会压缩保存，重启后仍保留。
- 每位私聊用户最多保存 100 MB，每个群最多保存 200 MB；达到上限后删除最旧记录。
- 私聊用户之间、不同群之间的聊天记录独立；同一群共用群内对话历史。
- AI 每次只参考最近一部分对话，不保证记起所有旧消息。
- 已绑定的选手资料长期保留，不随聊天记录淘汰。追踪名单在机器人内共用，可询问“目前记录了哪些 Dota 用户？”查看。
- AI 只可调用固定的只读查询工具，不能在对话中新增或修改选手绑定。

AI 会结合当前消息和对话上下文，自主判断是否调用工具。玩家资料只能按已绑定昵称查询；单场比赛、英雄和战队查询接受对应正整数 ID。支持玩家资料、胜负、英雄池、队友、统计、段位与排名，单场摘要，英雄克制与时长，战队资料，以及职业、直播和公开比赛摘要。任意 URL、SQL、OpenDota 写操作、搜索和解析任务均不开放。

`DOTA_AGENT_MAX_TOOL_CALLS` 控制单次 AI 对话最多执行多少次工具调用，默认值为 `4`，必须是正整数。Docker Compose 会通过 `.env` 将其传入容器。

机器人只能保存它实际收到的消息，无法记录未收到的群消息。
