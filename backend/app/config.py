"""
全局配置与启动校验

集中管理 JWT 密钥、签名参数与后端 TLS 配置，并在**导入阶段**做「不安全配置」拦截：
SECRET_KEY 缺失或仍是源码里的示例值时，宁可拒绝启动，也不允许带着公开密钥对外服务。
"""
import os
from typing import Optional

# 仓库中出现过的示例密钥：任何人拿到源码就能用它伪造 token，必须视为无效配置
INSECURE_SECRET_KEYS = {
    "your-secret-key-change-in-production",
    "your-secret-key",
    "changeme",
    "secret",
    "test",
}

# 密钥最小长度（HS256 的密钥强度直接决定 token 能否被离线爆破）
MIN_SECRET_KEY_LENGTH = 32

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BACKEND_ROOT, ".env")


class InsecureConfigError(RuntimeError):
    """配置不合格：继续启动会导致安全边界失效"""


def load_env_file(path: str = ENV_FILE) -> None:
    """把 backend/.env 读进环境变量（已存在的环境变量优先，便于容器/CI 覆盖）

    只做最简 KEY=VALUE 解析，避免为此引入 python-dotenv 依赖。
    """
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def validate_secret_key(key: Optional[str]) -> str:
    """校验 JWT 密钥强度，不合格直接抛错（返回去除空白后的密钥）"""
    if not key or not key.strip():
        raise InsecureConfigError(
            "SECRET_KEY 未设置。JWT 签名密钥必须由部署环境提供，否则任何人都能伪造登录态：\n"
            f"  1) 复制示例配置：{ENV_FILE}.example  ->  {ENV_FILE}\n"
            "  2) 生成随机密钥：python -c \"import secrets;print(secrets.token_urlsafe(48))\"\n"
            "  3) 在 .env 中填入 SECRET_KEY=<上一步生成的值>"
        )
    key = key.strip()
    if key in INSECURE_SECRET_KEYS:
        raise InsecureConfigError(
            "SECRET_KEY 仍是源码中的示例默认值，任何拿到源码的人都能伪造 token，等同于没有鉴权。"
            "请改为随机生成的值。"
        )
    if len(key) < MIN_SECRET_KEY_LENGTH:
        raise InsecureConfigError(
            f"SECRET_KEY 太短（当前 {len(key)} 字符，至少需要 {MIN_SECRET_KEY_LENGTH} 字符）。"
        )
    return key


def load_secret_key() -> str:
    """从 .env / 环境变量读取并校验 SECRET_KEY"""
    load_env_file()
    return validate_secret_key(os.getenv("SECRET_KEY"))


def load_tls_config() -> dict:
    """解析后端 TLS 证书配置，返回可直接展开给 uvicorn 的参数

    - 证书与私钥都配置且文件存在 → 返回 {"ssl_certfile":..., "ssl_keyfile":...}，对外即 https/wss；
    - 两者都未配置 → 返回 {}（保持 http，仅适用于本机调试）；
    - 只配了一个、或文件不存在 → 直接报错，避免「以为开了 https 其实没开」。
    """
    load_env_file()
    cert = (os.getenv("SSL_CERTFILE") or "").strip()
    key = (os.getenv("SSL_KEYFILE") or "").strip()

    if not cert and not key:
        return {}
    if not cert or not key:
        raise InsecureConfigError("SSL_CERTFILE 与 SSL_KEYFILE 必须同时配置。")

    for path, name in ((cert, "SSL_CERTFILE"), (key, "SSL_KEYFILE")):
        if not os.path.isfile(path):
            raise InsecureConfigError(f"{name} 指向的证书文件不存在：{path}")
    return {"ssl_certfile": cert, "ssl_keyfile": key}


# TURN 时效凭证的默认有效期（秒）：与 coturn use-auth-secret 的 REST 规范一致
DEFAULT_TURN_TTL_SECONDS = 600


def _split_urls(raw: Optional[str]) -> list:
    """把逗号分隔的地址串切成列表（忽略空白项）"""
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _check_url_scheme(urls: list, name: str, allowed: tuple) -> None:
    """校验地址协议前缀，避免把 http:// 之类写进来后「静默失效」"""
    for url in urls:
        if not url.startswith(allowed):
            raise InsecureConfigError(
                f"{name} 中的地址协议不合法：{url}（应为 {'/'.join(allowed)} 开头）"
            )


def load_ice_config() -> dict:
    """解析 ICE（STUN/TURN）配置，供 /api/v1/config/ice 统一下发

    - ICE_STUN_URLS：逗号分隔的 stun:/stuns: 地址。纯局域网部署留空即可：
      不下发任何 STUN 时浏览器只用 host 候选直连（最快），
      也避免等待不可达的公网 STUN 造成「入会慢、首帧延迟高」。
    - TURN_URLS / TURN_SECRET：必须成对配置。只配一个直接报错，
      否则会出现「以为能中继其实没密钥」或「中继无凭证开放」两类静默故障。
      TURN_SECRET 即 coturn 的 static-auth-secret，服务端据此为每个用户签发
      **时效凭证**，而不是下发长期密码。
    - TURN_TTL：凭证有效期（秒），默认 600。
    """
    load_env_file()
    stun_urls = _split_urls(os.getenv("ICE_STUN_URLS"))
    turn_urls = _split_urls(os.getenv("TURN_URLS"))
    turn_secret = (os.getenv("TURN_SECRET") or "").strip()
    ttl_raw = (os.getenv("TURN_TTL") or "").strip()

    if bool(turn_urls) != bool(turn_secret):
        raise InsecureConfigError(
            "TURN_URLS 与 TURN_SECRET 必须成对配置：只配一个会让中继要么不可用，"
            "要么退化成无凭证的开放中继。"
        )

    _check_url_scheme(stun_urls, "ICE_STUN_URLS", ("stun:", "stuns:"))
    _check_url_scheme(turn_urls, "TURN_URLS", ("turn:", "turns:"))

    ttl = DEFAULT_TURN_TTL_SECONDS
    if ttl_raw:
        try:
            ttl = int(ttl_raw)
        except ValueError:
            raise InsecureConfigError(f"TURN_TTL 必须是整数秒，当前值：{ttl_raw}")
        if ttl <= 0:
            raise InsecureConfigError("TURN_TTL 必须大于 0。")

    return {
        "stun_urls": stun_urls,
        "turn_urls": turn_urls,
        "turn_secret": turn_secret,
        "ttl": ttl,
    }


# 导入即校验：配置不合格时应用直接起不来，而不是带着默认密钥对外服务
SECRET_KEY = load_secret_key()


# ============ 录制配额（磁盘写满防护） ============
# 录制分片端点接收的是原始二进制流，若没有大小上限，一个已登录的会议创建者
# 就能用脚本持续灌分片把服务器磁盘写满，进而拖垮数据库与整个服务。
# 三层阈值：单片（挡住单个巨块请求）→ 单场（挡住长时间灌同一场录制）
#          → 全局（挡住不停开新会议绕过单场限制）。单位均为字节。
DEFAULT_REC_MAX_CHUNK_BYTES = 16 * 1024 * 1024            # 单片 16MB（1s 分片实际仅数十 KB）
DEFAULT_REC_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024     # 单场录制 2GB
DEFAULT_REC_MAX_STORAGE_BYTES = 20 * 1024 * 1024 * 1024  # recordings/ 目录总计 20GB


def _load_positive_int(name: str, default: int) -> int:
    """读取正整数型环境变量，缺省取默认值，非法值直接报错"""
    load_env_file()
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise InsecureConfigError(f"{name} 必须是整数（字节数），当前值：{raw}")
    if value <= 0:
        raise InsecureConfigError(f"{name} 必须大于 0，当前值：{raw}")
    return value


def load_recording_quota() -> dict:
    """解析录制配额，返回 max_chunk_bytes / max_total_bytes / max_storage_bytes

    必须逐级放宽，否则配置自相矛盾（例如单片上限高于单场上限时，
    单场限制会先被触发，单片限制形同虚设），因此一并校验并直接报错。
    """
    quota = {
        "max_chunk_bytes": _load_positive_int("REC_MAX_CHUNK_BYTES", DEFAULT_REC_MAX_CHUNK_BYTES),
        "max_total_bytes": _load_positive_int("REC_MAX_TOTAL_BYTES", DEFAULT_REC_MAX_TOTAL_BYTES),
        "max_storage_bytes": _load_positive_int("REC_MAX_STORAGE_BYTES", DEFAULT_REC_MAX_STORAGE_BYTES),
    }
    if not (quota["max_chunk_bytes"] <= quota["max_total_bytes"] <= quota["max_storage_bytes"]):
        raise InsecureConfigError(
            "录制配额必须逐级放宽：REC_MAX_CHUNK_BYTES <= REC_MAX_TOTAL_BYTES <= REC_MAX_STORAGE_BYTES，"
            f"当前为 {quota['max_chunk_bytes']} / {quota['max_total_bytes']} / {quota['max_storage_bytes']}"
        )
    return quota


# ============ 视频席位上限（Mesh 上行带宽防护） ============
# 全互联（Mesh）架构下，每个参会者的上行 = 「同时开摄像头的人数 − 1」路独立编码，
# 每多一个开视频的人，所有人就要多编码一路、多上传一份，瓶颈是「同时开视频人数」
# 而不是在线人数。因此必须给同时开摄像头的人数控一个硬上限，
# 否则 16 人会议会把每个参会者的上行带宽与 CPU 一起打满。
DEFAULT_VIDEO_SEAT_LIMIT = 4


def load_video_seat_limit() -> int:
    """解析同时开摄像头的席位上限

    下限必须是 1：配成 0 会让任何人都无法开视频，且前端会显示「席位已满」却无人占用。
    """
    return _load_positive_int("MEETING_VIDEO_SEAT_LIMIT", DEFAULT_VIDEO_SEAT_LIMIT)


# ============ 单会议总人数硬上限（Mesh 容量护栏） ============
# Mesh 全互联下每人要维护「人数 − 1」条连接，上行带宽与 CPU 随人数快速增长：
# 16 人时每人已要维护 15 条连接，开视频者的上行 = 15 × 单路码率。
# 没有上限时第 17、50 人照样能进，会把所有人的音视频一起拖垮。
# 该上限作用于「会议总人数」（含等候室），在 /join 与 WebSocket 鉴权两处强制。
DEFAULT_MAX_PARTICIPANTS = 16


def load_max_participants() -> int:
    """解析单会议总人数上限

    下限必须是 1：至少允许建会者自己入会（建会即占 1 个名额）。
    """
    return _load_positive_int("MEETING_MAX_PARTICIPANTS", DEFAULT_MAX_PARTICIPANTS)


# ============ 认证接口限流（撞库 / 批量注册 / 验证码轰炸防护） ============
# 认证接口原先完全不限速：攻击者可以无限次撞库 /login、反复调用 /send-code
# 给任意注册邮箱刷验证码、或用脚本批量注册账号。以下阈值按 60s 滑动窗口计数，
# 任一维度达到即返回 429。注意共享出口 IP（教室 / 机房）下收紧 IP 阈值可能误伤，
# 调整前先按实际使用人数估算。
#
# 局限：计数保存在**单进程内存**中（app/rate_limit.py），多进程 / 多实例部署时
# 每个进程各算各的，实际放行量会被进程数放大；改为共享存储（Redis）后才能全局生效。
DEFAULT_AUTH_LOGIN_LIMIT_PER_USER = 10        # /login：单账号 60s 内最多 10 次
DEFAULT_AUTH_LOGIN_LIMIT_PER_IP = 30          # /login：单 IP 60s 内最多 30 次
DEFAULT_AUTH_REGISTER_LIMIT_PER_IP = 10       # /register：单 IP 60s 内最多 10 次
DEFAULT_AUTH_SEND_CODE_LIMIT_PER_EMAIL = 5    # /send-code：单邮箱 60s 内最多 5 次
DEFAULT_AUTH_SEND_CODE_LIMIT_PER_IP = 15      # /send-code：单 IP 60s 内最多 15 次
DEFAULT_AUTH_RESET_LIMIT_PER_EMAIL = 30       # /reset-password：单邮箱 60s 内最多 30 次
DEFAULT_AUTH_RESET_LIMIT_PER_IP = 30          # /reset-password：单 IP 60s 内最多 30 次
DEFAULT_AUTH_CHANGE_PWD_LIMIT_PER_USER = 10   # /password：单账号 60s 内最多 10 次


def load_auth_rate_limits() -> dict:
    """解析认证接口限流阈值（每个 60s 窗口内的最多次数，见 auth_router.AUTH_LIMITS）"""
    return {
        "login_per_user": _load_positive_int(
            "AUTH_LOGIN_LIMIT_PER_USER", DEFAULT_AUTH_LOGIN_LIMIT_PER_USER),
        "login_per_ip": _load_positive_int(
            "AUTH_LOGIN_LIMIT_PER_IP", DEFAULT_AUTH_LOGIN_LIMIT_PER_IP),
        "register_per_ip": _load_positive_int(
            "AUTH_REGISTER_LIMIT_PER_IP", DEFAULT_AUTH_REGISTER_LIMIT_PER_IP),
        "send_code_per_email": _load_positive_int(
            "AUTH_SEND_CODE_LIMIT_PER_EMAIL", DEFAULT_AUTH_SEND_CODE_LIMIT_PER_EMAIL),
        "send_code_per_ip": _load_positive_int(
            "AUTH_SEND_CODE_LIMIT_PER_IP", DEFAULT_AUTH_SEND_CODE_LIMIT_PER_IP),
        "reset_per_email": _load_positive_int(
            "AUTH_RESET_LIMIT_PER_EMAIL", DEFAULT_AUTH_RESET_LIMIT_PER_EMAIL),
        "reset_per_ip": _load_positive_int(
            "AUTH_RESET_LIMIT_PER_IP", DEFAULT_AUTH_RESET_LIMIT_PER_IP),
        "change_pwd_per_user": _load_positive_int(
            "AUTH_CHANGE_PWD_LIMIT_PER_USER", DEFAULT_AUTH_CHANGE_PWD_LIMIT_PER_USER),
    }