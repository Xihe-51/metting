"""
严重-14 ICE 配置统一下发 + 中-9（后半）WS 不再用 URL 传 token —— 契约与攻击复现

【修复前的问题】
1. 前端把 stun.l.google.com 写死在 createPeerConnection 里：
   - 局域网里这个地址通常不可达，浏览器会一直等 srflx 候选，
     导致「入会慢、首帧延迟高」，同网段本可 host 直连；
   - 换部署环境（有 TURN / 无 TURN）必须改代码重新构建。
2. WebSocket 用 URL query 传 token（?token=...）：URL 会进入 access log、
   浏览器历史与代理记录，token 一旦落盘即可被复用；且 JWT 无时效约束时
   泄漏即长期有效。

【修复要点】
- 新增 GET /api/v1/config/ice（需登录），统一下发 STUN/TURN：
  纯局域网下 iceServers 为空数组（host-only 直连最快）；
  配了 TURN 时为每个用户签发**时效凭证**（coturn use-auth-secret REST 规范）。
- WS 改为 accept 后首帧 {"type":"auth","token":...} 鉴权，URL 不再携带 token。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s18_ice_config.py -v
"""
import base64
import hashlib
import hmac
import os
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import BACKEND_ROOT, create_meeting, make_user, participant_id_of  # noqa: E402
from app import config  # noqa: E402
from app.config import (  # noqa: E402
    DEFAULT_TURN_TTL_SECONDS,
    InsecureConfigError,
    load_ice_config,
)
from app.ice import build_ice_config, make_turn_credentials  # noqa: E402

FRONT_MEETING_VIEW = os.path.abspath(
    os.path.join(BACKEND_ROOT, "..", "front", "src", "views", "MeetingView.vue")
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture()
def no_env_file(monkeypatch):
    """屏蔽 backend/.env，避免开发机本地配置影响断言（环境变量由用例自己控制）"""
    monkeypatch.setattr(config, "load_env_file", lambda *a, **k: None)


@pytest.fixture()
def clean_ice_env(monkeypatch, no_env_file):
    """把 4 个 ICE 相关环境变量清成「未配置」状态"""
    for name in ("ICE_STUN_URLS", "TURN_URLS", "TURN_SECRET", "TURN_TTL"):
        monkeypatch.delenv(name, raising=False)


def _expected_credential(secret: str, username: str) -> str:
    """独立重算 credential（不调用被测函数），验证确实是 HMAC-SHA1"""
    digest = hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


# ---------------- 配置解析 ----------------

def test_ice_defaults_to_host_only(clean_ice_env):
    """什么都不配 → host-only：这是纯局域网部署的默认形态"""
    cfg = load_ice_config()
    assert cfg["stun_urls"] == []
    assert cfg["turn_urls"] == []
    assert cfg["ttl"] == DEFAULT_TURN_TTL_SECONDS


def test_ice_rejects_turn_urls_without_secret(monkeypatch, clean_ice_env):
    """只配 TURN_URLS 没配密钥 → 报错（否则中继要么不可用，要么无凭证开放）"""
    monkeypatch.setenv("TURN_URLS", "turn:192.168.1.5:3478")
    with pytest.raises(InsecureConfigError):
        load_ice_config()


def test_ice_rejects_secret_without_turn_urls(monkeypatch, clean_ice_env):
    """只配密钥没配 TURN_URLS → 同样报错（避免「以为能中继其实没配」）"""
    monkeypatch.setenv("TURN_SECRET", "x" * 32)
    with pytest.raises(InsecureConfigError):
        load_ice_config()


def test_ice_rejects_wrong_url_scheme(monkeypatch, clean_ice_env):
    """协议前缀写错必须报错，而不是被静默忽略"""
    monkeypatch.setenv("ICE_STUN_URLS", "http://stun.example.com:3478")
    with pytest.raises(InsecureConfigError):
        load_ice_config()

    monkeypatch.delenv("ICE_STUN_URLS", raising=False)
    monkeypatch.setenv("TURN_URLS", "stun:192.168.1.5:3478")
    monkeypatch.setenv("TURN_SECRET", "x" * 32)
    with pytest.raises(InsecureConfigError):
        load_ice_config()


def test_ice_rejects_invalid_ttl(monkeypatch, clean_ice_env):
    """TTL 非法（非整数 / 非正）→ 报错"""
    monkeypatch.setenv("ICE_STUN_URLS", "stun:stun.example.com:3478")

    monkeypatch.setenv("TURN_TTL", "abc")
    with pytest.raises(InsecureConfigError):
        load_ice_config()

    monkeypatch.setenv("TURN_TTL", "0")
    with pytest.raises(InsecureConfigError):
        load_ice_config()


def test_ice_parses_multiple_urls_and_trims(monkeypatch, clean_ice_env):
    """逗号分隔的多个地址必须被正确切开，并忽略空白项"""
    monkeypatch.setenv("ICE_STUN_URLS", " stun:a.example.com:3478 , stun:b.example.com:3478 ,")
    monkeypatch.setenv("TURN_URLS", "turn:192.168.1.5:3478,turns:192.168.1.5:5349")
    monkeypatch.setenv("TURN_SECRET", "x" * 32)
    monkeypatch.setenv("TURN_TTL", "120")

    cfg = load_ice_config()
    assert cfg["stun_urls"] == ["stun:a.example.com:3478", "stun:b.example.com:3478"]
    assert cfg["turn_urls"] == ["turn:192.168.1.5:3478", "turns:192.168.1.5:5349"]
    assert cfg["ttl"] == 120


# ---------------- 时效凭证 ----------------

def test_turn_credential_is_hmac_sha1_of_username():
    """凭证 = base64(HMAC-SHA1(secret, username))，username = "<过期时间戳>:<用户标识>" """
    username, credential = make_turn_credentials("secret-key", 42, ttl=600, now=1_700_000_000)

    exp_str, sep, identity = username.partition(":")
    assert sep == ":" and identity == "42"
    assert int(exp_str) == 1_700_000_000 + 600
    assert credential == _expected_credential("secret-key", username)


def test_turn_credential_expiry_advances_with_ttl_and_is_reproducible():
    """同参数可复现；TTL 变化必须直接体现在过期时间戳上"""
    _, c1 = make_turn_credentials("k", 7, ttl=600, now=1_700_000_000)
    _, c2 = make_turn_credentials("k", 7, ttl=600, now=1_700_000_000)
    assert c1 == c2  # 无随机成分，便于 coturn 侧独立校验

    u3, _ = make_turn_credentials("k", 7, ttl=120, now=1_700_000_000)
    assert u3.startswith(str(1_700_000_000 + 120))


def test_turn_credential_differs_per_user():
    """不同用户必须拿到不同凭证，避免互相冒用"""
    u1, c1 = make_turn_credentials("k", 1, ttl=600, now=1_700_000_000)
    u2, c2 = make_turn_credentials("k", 2, ttl=600, now=1_700_000_000)
    assert u1 != u2 and c1 != c2


# ---------------- build_ice_config 形状 ----------------

def test_build_ice_config_host_only_has_empty_servers(clean_ice_env):
    """host-only：iceServers 为空数组，且不含任何凭证字段"""
    cfg = load_ice_config()
    result = build_ice_config(identity=1, cfg=cfg)

    assert result["iceServers"] == []
    assert result["iceTransportPolicy"] == "all"
    assert result["ttl"] == DEFAULT_TURN_TTL_SECONDS
    assert "credential" not in str(result) and "password" not in str(result)


def test_build_ice_config_attaches_credentials_only_to_turn(clean_ice_env):
    """STUN 项不带凭证；TURN 项带时效凭证"""
    cfg = {
        "stun_urls": ["stun:stun.example.com:3478"],
        "turn_urls": ["turn:192.168.1.5:3478"],
        "turn_secret": "shared-secret",
        "ttl": 600,
    }
    result = build_ice_config(identity=99, cfg=cfg, now=1_700_000_000)

    stun_entries = [s for s in result["iceServers"] if s["urls"].startswith("stun:")]
    turn_entries = [s for s in result["iceServers"] if s["urls"].startswith("turn:")]

    assert stun_entries == [{"urls": "stun:stun.example.com:3478"}]
    assert len(turn_entries) == 1
    turn = turn_entries[0]
    assert turn["username"] == f"{1_700_000_000 + 600}:99"
    assert turn["credential"] == _expected_credential("shared-secret", turn["username"])


# ---------------- 接口层：需要登录 ----------------

def test_ice_endpoint_requires_auth(client, clean_ice_env):
    """匿名请求必须 401：TURN 凭证虽有 TTL，也不应向未登录访客签发"""
    resp = client.get("/api/v1/config/ice")
    assert resp.status_code == 401


def test_ice_endpoint_host_only_hides_credentials(client, session_factory, clean_ice_env):
    """局域网默认形态：iceServers 为空，响应里不得出现任何凭证字样"""
    _, _, headers = make_user(session_factory, "ice_s18")

    resp = client.get("/api/v1/config/ice", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["iceServers"] == []
    assert data["iceTransportPolicy"] == "all"

    lowered = resp.text.lower()
    assert "credential" not in lowered
    assert "password" not in lowered


def test_ice_endpoint_issues_turn_credentials_per_user(
    client, session_factory, monkeypatch, clean_ice_env
):
    """配了 TURN：凭证必须绑定当前用户且带有效期（而不是下发长期密码）"""
    monkeypatch.setenv("TURN_URLS", "turn:192.168.1.5:3478")
    monkeypatch.setenv("TURN_SECRET", "s18-shared-secret")
    monkeypatch.setenv("TURN_TTL", "300")

    user_id, _, headers = make_user(session_factory, "ice_turn_s18")
    before = int(time.time())

    resp = client.get("/api/v1/config/ice", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    turn_entries = [s for s in data["iceServers"] if s["urls"].startswith("turn:")]
    assert len(turn_entries) == 1
    turn = turn_entries[0]

    exp_str, _, identity = turn["username"].partition(":")
    assert identity == str(user_id)                       # 凭证绑定到具体用户
    assert before + 300 <= int(exp_str) <= int(time.time()) + 300 + 5
    assert turn["credential"] == _expected_credential("s18-shared-secret", turn["username"])
    assert "password" not in resp.text.lower()            # 绝不下发长期密码


# ---------------- 前端契约（源码扫描） ----------------

def test_frontend_has_no_hardcoded_stun_and_uses_ice_api():
    """前端不得再硬编码公网 STUN，必须改为向 /config/ice 拉取"""
    src = _read(FRONT_MEETING_VIEW)
    assert "stun.l.google.com" not in src
    assert "stun1.l.google.com" not in src
    assert "/config/ice" in src


def test_frontend_ws_url_carries_no_token_and_sends_auth_frame():
    """前端 WS 地址不得再拼 token；改为连接建立后发鉴权帧"""
    src = _read(FRONT_MEETING_VIEW)
    assert "&token=" not in src
    assert "'auth'" in src or '"auth"' in src


# ---------------- WS：token 不再走 URL（攻击面复现） ----------------

def _meeting_with_host(client, session_factory, username):
    _, token, headers = make_user(session_factory, username)
    data = create_meeting(client, headers, title="WS 鉴权帧验证")
    meeting_no = data["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, username)
    return meeting_no, pid, token


def test_ws_url_token_is_ignored(client, session_factory):
    """把合法 token 塞进 URL 也不再被接受：首帧不是 auth 帧 → 立即 1008 断开"""
    meeting_no, pid, token = _meeting_with_host(client, session_factory, "wsurl_s18")

    with client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={pid}&token={token}"
    ) as ws:
        ws.send_json({"type": "ping"})  # 首帧不是 auth：URL 里的 token 不会被采信
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_json()
        assert ei.value.code == 1008


def test_ws_rejects_forged_auth_frame(client, session_factory):
    """攻击复现：伪造 token 放在首帧里同样被拒（1008），且不泄露任何会议数据"""
    meeting_no, pid, _ = _meeting_with_host(client, session_factory, "wsforged_s18")

    with client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={pid}"
    ) as ws:
        ws.send_json({"type": "auth", "token": "forged-token"})
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_json()
        assert ei.value.code == 1008


def test_ws_accepts_valid_token_via_auth_frame(client, session_factory):
    """正向对照：首帧携带合法 token 才拿到会议数据（防止校验写成永远拒绝）"""
    meeting_no, pid, token = _meeting_with_host(client, session_factory, "wsok_s18")

    with client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={pid}"
    ) as ws:
        ws.send_json({"type": "auth", "token": token})
        first = ws.receive_json()
        assert first["type"] in ("participants_list", "waiting_room")