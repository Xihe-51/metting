# 局域网视频会议系统 - 后端

FastAPI + SQLite + WebSocket

## 项目结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI 入口
│   ├── config.py           # 全局配置（SECRET_KEY 启动校验、TLS 配置）
│   ├── database.py         # SQLAlchemy 配置
│   ├── models.py           # 数据库模型
│   ├── websocket_manager.py # WebSocket 连接管理
│   └── routers/
│       ├── __init__.py
│       └── meeting_router.py # 会议路由
├── scripts/
│   ├── gen_dev_cert.ps1    # 生成局域网自签 HTTPS 证书
│   └── verify_https_wss.py # 验证 https / wss 链路
├── recordings/             # 录制文件存储目录
├── static/                 # 静态文件目录
├── meeting.db              # SQLite 数据库（运行后自动创建）
├── .env.example            # 环境变量模板（复制为 .env 后填写 SECRET_KEY）
├── requirements.txt        # Python 依赖
└── run.py                  # 启动脚本
```

## 安装依赖

```bash
# 创建虚拟环境（可选）
python -m venv venv
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

## 配置（首次启动必读）

JWT 签名密钥**必须由部署环境提供**：未设置、仍是源码示例值、或长度不足 32 字符时，
后端会直接拒绝启动（而不是带着公开密钥对外服务）。

```powershell
# 1) 生成一份本地配置
Copy-Item .env.example .env

# 2) 生成随机密钥并填入 .env 的 SECRET_KEY
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

`.env` 已被 `.gitignore` 忽略，请勿提交；`.env.example` 仅作模板。

## 运行

```bash
python run.py
```

服务启动后：
- API 地址: http://localhost:8000
- API 文档: http://localhost:8000/docs （Swagger UI）
- WebSocket: ws://localhost:8000/ws/{meeting_no}

## 局域网 HTTPS 部署（摄像头/麦克风必需）

浏览器只在**安全上下文**（HTTPS 或 localhost）下暴露 `navigator.mediaDevices`：
用 `http://192.168.x.x` 打开页面时 `getUserMedia` 是 undefined，摄像头和麦克风全部不可用。
因此局域网内必须走 HTTPS，WebSocket 同步升级为 wss。

```powershell
# 1) 生成自签证书（覆盖 localhost + 本机全部局域网 IPv4），依赖 openssl（Git for Windows 自带）
powershell -ExecutionPolicy Bypass -File scripts\gen_dev_cert.ps1

# 2) 在 backend\.env 中追加证书路径（脚本执行完会打印现成的两行）
#    SSL_CERTFILE=D:/meeting/certs/dev-cert.pem
#    SSL_KEYFILE=D:/meeting/certs/dev-key.pem

# 3) 重启后端：输出会显示 https:// 与 wss:/
python run.py

# 4) 自证链路（明文应被拒、https 应 200、伪造 token 的 wss 应被拒）
python scripts\verify_https_wss.py --host 192.168.1.5
```

前端两种用法：

- 生产形态（`main.py` 托管 `front/dist`）：TLS 由后端 uvicorn 终止，前后端同源，直接访问 `https://<本机IP>:8000`。
- 开发形态（`npm run dev`）：vite 会自动读取同一套 `certs/` 证书并启用 HTTPS，
  `/api` 代理已开 `ws: true`；看不到证书时会退回 HTTP 并在控制台给出提示。

自签证书浏览器不认，首次访问需在提示页选择“继续访问”；若希望局域网其他设备免警告，
把 `certs/dev-cert.pem` 安装为受信任根证书即可。

## TURN 中继部署（可选：仅跨网段 / NAT 场景需要）

**纯局域网部署无需 TURN**：同网段设备之间用 host 候选直连最快，此时
`GET /api/v1/config/ice` 下发的 `iceServers` 是空数组，前端不会配置任何 STUN/TURN。

只有「设备与服务器不在同一网段」「无线 AP 开了客户端隔离」这类无法直连的场景才需要中继：

```powershell
# 1) 安装 coturn（Linux: apt/yum install coturn；Windows 可用 choco/scoop 或官方二进制）
# 2) 生成仅服务端知道的共享密钥，同时填入 deploy\coturn\turnserver.conf 与 backend\.env
python -c "import secrets;print(secrets.token_urlsafe(32))"

# 3) 用仓库自带配置启动 coturn（含 use-auth-secret、内网跳板防护）
coturn -c deploy\coturn\turnserver.conf

# 4) 在 backend\.env 中追加（密钥须与 coturn 一致），然后重启后端
#    TURN_URLS=turn:192.168.1.5:3478
#    TURN_SECRET=<上一步生成的密钥>
#    TURN_TTL=600
```

后端**不下发长期密码**，而是按 coturn `use-auth-secret` 的 REST 规范为每个用户签发
带有效期的临时凭证（`username="<过期时间戳>:<用户ID>"`，
`credential=base64(HMAC-SHA1(secret, username))`），凭证即使泄漏也只在 TTL 内可用。

## 媒体容量护栏与部署建议（单会议 16 人）

系统采用 Mesh（全互联）架构，**单会议按 16 人设计**。Mesh 下每人的上行带宽
≈ 对端数 × 单路码率、浏览器要同时维护「人数 − 1」条连接，因此三条硬护栏都已落在
代码里，而不是只写在文档上：

| 护栏 | 默认值 | 配置项 | 超限行为 |
|------|--------|--------|----------|
| 单会议总人数（含等候室） | 16 | `MEETING_MAX_PARTICIPANTS` | `/join` 返回 409「会议人数已达上限」；断线重连同受此限 |
| 同时开摄像头人数 | 4 | `MEETING_VIDEO_SEAT_LIMIT` | 点开摄像头者收到「席位已满」，保持关闭；有席位释放后可再开 |
| 自动画质封顶 | 对端 ≥ 12 时封顶标清 | 前端内置（`AUTO_QUALITY_PEER_CAP`） | 自动模式不再升到高清，防止上行突发 |

16 人 / 4 席下的量级参考（单人上行、局域网内该会议的视频聚合流量）：

| 画质 | 单路码率 | 开视频者上行（15 对端） | 会议视频聚合（4 席 × 15 副本） |
|------|---------|------------------------|-------------------------------|
| 流畅 | 200 kbps | ≈ 3 Mbps | ≈ 12 Mbps |
| 标清 | 600 kbps | ≈ 9 Mbps | ≈ 36 Mbps |
| 高清 | 2 Mbps | ≈ 30 Mbps | ≈ 120 Mbps（百兆局域网打爆） |

部署建议：

- **有线网络**：席位可保持 4；开视频的人建议有线接入。
- **WiFi 为主**：席位建议降到 2–3（`MEETING_VIDEO_SEAT_LIMIT=2`），避免空口拥塞。
- **百兆局域网**：建议全员使用「流畅」画质，或同时把席位降到 2。
- 提高任何上限前，先确认所有参会者的上行带宽与 CPU 能承担对应增量。

## 接口限流与滥用防护

除媒体容量护栏外，以下接口带频率限制（60 秒滑动窗口，超限返回 429）：

| 接口 | 限流维度 | 默认阈值（次/60s） | 配置项 |
|------|---------|-------------------|--------|
| `/api/v1/auth/login` | 单账号 / 单 IP | 10 / 30 | `AUTH_LOGIN_LIMIT_PER_USER` / `AUTH_LOGIN_LIMIT_PER_IP` |
| `/api/v1/auth/register` | 单 IP | 10 | `AUTH_REGISTER_LIMIT_PER_IP` |
| `/api/v1/auth/send-code` | 单邮箱 / 单 IP | 5 / 15 | `AUTH_SEND_CODE_LIMIT_PER_EMAIL` / `AUTH_SEND_CODE_LIMIT_PER_IP` |
| `/api/v1/auth/reset-password` | 单邮箱 / 单 IP | 30 / 30 | `AUTH_RESET_LIMIT_PER_EMAIL` / `AUTH_RESET_LIMIT_PER_IP` |
| `/api/v1/auth/password` | 单账号 | 10 | `AUTH_CHANGE_PWD_LIMIT_PER_USER` |
| `/api/v1/meetings/{no}/join` | 单账号 / 单 IP | 30 / 90 | 代码内置（会议号枚举防护） |
| `/api/v1/meetings/{no}` | 单账号 / 单 IP | 60 / 180 | 代码内置 |
| WebSocket 消息 | 单连接分档 | 见 `WS_RATE_LIMITS` | 代码内置 |

录制分片另有三层磁盘配额（单片 / 单场 / 目录总量，见 `.env.example` 的
`REC_MAX_*`），超限返回 413 且不落盘。

**已知局限（重要）**：限流计数保存在**单进程内存**中（`app/rate_limit.py`），
只适用于当前的单进程 uvicorn 部署。若改为多进程 / 多实例（`--workers > 1`、
gunicorn、容器多副本），每个进程各自计数，实际放行量 ≈ 阈值 × 进程数；
需要全局精确限流时必须替换为 Redis 等共享存储。录制配额基于磁盘实际占用与
文件系统校验，不受多进程影响。

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/meetings` | POST | 创建会议 |
| `/api/v1/meetings/{no}/join` | POST | 加入会议 |
| `/api/v1/meetings/{no}` | GET | 获取会议信息 |
| `/api/v1/meetings/{no}/end` | POST | 结束会议 |
| `/api/v1/meetings` | GET | 获取会议列表 |
| `/api/v1/config/ice` | GET | 下发 STUN/TURN 配置（需登录） |
| `/api/v1/ws/{meeting_no}?participant_id=` | WebSocket | 实时通信（连接后首帧 `{"type":"auth","token":"..."}` 鉴权，token 不再走 URL） |

## 测试

打开浏览器访问 http://localhost:8000/docs 可以看到自动生成的 API 文档，可以直接测试接口。