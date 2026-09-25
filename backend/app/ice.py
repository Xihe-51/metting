"""
ICE（STUN/TURN）配置构造与下发

前端不再硬编码 stun.l.google.com，而是登录后向 /api/v1/config/ice 拉取：
- 纯局域网：默认不下发任何 STUN/TURN（iceServers 为空数组）。此时浏览器只用
  host 候选直连——同网段最快，也避免等待不可达的公网 STUN 造成「入会慢、首帧延迟高」；
- 跨网段 / NAT：运维配置 TURN 后，这里为每个用户签发**时效凭证**
  （coturn use-auth-secret 的 REST 规范：username="<过期时间戳>:<用户标识>"，
  credential=base64(HMAC-SHA1(secret, username))），凭证即使泄漏也只在 TTL 内有效。

本模块只做纯计算，便于单测独立重算 HMAC 校验凭证正确性。
"""
import base64
import hashlib
import hmac
import time
from typing import Optional, Tuple

from app.config import load_ice_config

# 候选策略保持 'all'：能直连就走直连（局域网 host 候选最快），连不通才回退中继
ICE_TRANSPORT_POLICY = "all"
# 预收集候选数量，减少首帧建立的等待
ICE_CANDIDATE_POOL_SIZE = 2


def make_turn_credentials(secret: str, identity, ttl: int,
                          now: Optional[float] = None) -> Tuple[str, str]:
    """按 coturn REST 规范生成时效凭证，返回 (username, credential)

    username 形如 "<过期时间戳>:<用户标识>"，coturn 会用同一 secret 重算 HMAC
    并校验时间戳未过期，因此无需在服务端保存任何凭证状态。
    """
    expiry = int(now if now is not None else time.time()) + int(ttl)
    username = f"{expiry}:{identity}"
    digest = hmac.new(
        secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1
    ).digest()
    return username, base64.b64encode(digest).decode("ascii")


def build_ice_config(identity, cfg: Optional[dict] = None,
                     now: Optional[float] = None) -> dict:
    """构造下发给前端的 ICE 配置

    iceServers 为空数组即表示「host-only」，前端不应再传 iceServers 属性。
    """
    cfg = cfg if cfg is not None else load_ice_config()

    ice_servers = [{"urls": url} for url in cfg["stun_urls"]]
    if cfg["turn_urls"]:
        username, credential = make_turn_credentials(
            cfg["turn_secret"], identity, cfg["ttl"], now=now
        )
        ice_servers.extend(
            {"urls": url, "username": username, "credential": credential}
            for url in cfg["turn_urls"]
        )

    return {
        "iceServers": ice_servers,
        "iceTransportPolicy": ICE_TRANSPORT_POLICY,
        "iceCandidatePoolSize": ICE_CANDIDATE_POOL_SIZE,
        "ttl": cfg["ttl"],
    }