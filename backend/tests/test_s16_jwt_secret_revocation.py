"""
严重-1 JWT 密钥与令牌吊销 —— 攻击复现脚本 + 修复后验证

【修复前的漏洞】
1. auth_router 里写死 `SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")`，
   仓库中又不存在 .env，于是所有部署实际上都在用这个公开密钥签名；
   **任何读过源码的人都能自签任意 user_id 的 token，直接接管账号**。
2. token 里没有 jti / iat，无法追踪单次签发，也没有任何吊销手段；
3. 改密码 / 重置密码后，旧 token 在有效期内照样能用（JWT 无状态带来的经典问题）。

【修复要点】
- app/config.py 在导入阶段就校验 SECRET_KEY：缺失、等于源码示例值、长度 < 32 —— 一律拒绝启动；
- 签发时写入 iat / jti，并把用户的 token_version 作为 ver 声明下发；
- 校验时比对 ver 与库中 token_version，改密码 / 重置密码会让 token_version 自增，
  此前签发的所有 token 立即失效（REST 与 WebSocket 共用同一条校验路径）。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s16_jwt_secret_revocation.py -v
"""
import time
from datetime import datetime, timedelta

import pytest
from jose import jwt
from starlette.websockets import WebSocketDisconnect

from conftest import (  # noqa: E402
    DEFAULT_PASSWORD,
    create_meeting,
    make_user,
    participant_id_of,
)
from app.config import (  # noqa: E402
    INSECURE_SECRET_KEYS,
    MIN_SECRET_KEY_LENGTH,
    ALGORITHM,
    InsecureConfigError,
    validate_secret_key,
)
from app.models import User, VerificationCode  # noqa: E402
from app.routers.auth_router import (  # noqa: E402
    SECRET_KEY,
    create_access_token,
    token_claims,
    verify_password,
)

# 修复前 auth_router 中写死的默认密钥：攻击者只要读过源码就能拿到
LEGACY_DEFAULT_SECRET = "your-secret-key-change-in-production"


# ---------------- 攻击复现：伪造 token ----------------

def test_forged_token_with_legacy_default_secret_is_rejected(client, session_factory):
    """攻击复现：用源码里的默认密钥自签 token，冒充任意用户

    修复前：/api/v1/auth/me 返回 200，攻击者拿到受害者身份；
    修复后：签名不匹配 → 401。
    """
    victim_id, legit_token, legit_h = make_user(session_factory, "victim_s16a")

    forged = jwt.encode(
        {"user_id": victim_id, "username": "victim_s16a"},
        LEGACY_DEFAULT_SECRET,
        algorithm=ALGORITHM,
    )
    assert forged != legit_token

    forged_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert forged_resp.status_code == 401, forged_resp.text

    # 负向对照：同一用户用真实密钥签发的 token 必须可用，
    # 证明 401 来自签名不匹配，而不是接口本身不可用（断言不是空过）
    ok_resp = client.get("/api/v1/auth/me", headers=legit_h)
    assert ok_resp.status_code == 200, ok_resp.text
    assert ok_resp.json()["data"]["id"] == victim_id


def test_service_actual_secret_is_not_the_legacy_default():
    """修复后：运行中的服务实例用的必须不是那个公开的默认密钥"""
    assert LEGACY_DEFAULT_SECRET not in SECRET_KEY
    assert len(SECRET_KEY) >= MIN_SECRET_KEY_LENGTH


# ---------------- 启动即校验：弱配置拒绝启动 ----------------

def test_validate_secret_key_rejects_weak_values():
    """配置不合格必须抛错（生产上表现为「拒绝启动」）"""
    # 1) 未设置 / 空字符串 / 纯空白
    for bad in (None, "", "   "):
        with pytest.raises(InsecureConfigError):
            validate_secret_key(bad)

    # 2) 源码中的示例默认值（每个都试一遍）
    for bad in INSECURE_SECRET_KEYS:
        with pytest.raises(InsecureConfigError):
            validate_secret_key(bad)

    # 3) 长度不足
    with pytest.raises(InsecureConfigError):
        validate_secret_key("a" * (MIN_SECRET_KEY_LENGTH - 1))


def test_validate_secret_key_accepts_strong_value():
    """长度达标且不是示例值时通过，并自动去掉首尾空白"""
    strong = "Zx9" * 20  # 60 字符
    assert validate_secret_key(strong) == strong
    assert validate_secret_key(f"  {strong}  ") == strong


# ---------------- token 声明：jti / iat ----------------

def test_token_carries_unique_jti_and_iat():
    """每个 token 都要有 iat 与唯一 jti（否则无法追踪单次签发）"""
    now = time.time()
    first = jwt.decode(create_access_token({"user_id": 1, "username": "u"}),
                       SECRET_KEY, algorithms=[ALGORITHM])
    second = jwt.decode(create_access_token({"user_id": 1, "username": "u"}),
                        SECRET_KEY, algorithms=[ALGORITHM])

    assert first["jti"] and second["jti"]
    assert first["jti"] != second["jti"]

    issued = datetime.utcfromtimestamp(first["iat"])
    assert abs((datetime.utcnow() - issued).total_seconds()) < 60
    assert now - 60 <= first["iat"] <= now + 60
    # exp 仍按配置的有效期下发
    assert 0 < first["exp"] - first["iat"] <= 24 * 3600


# ---------------- 改密码 / 重置密码 → 旧 token 立即失效 ----------------

def _token_version(session_factory, user_id):
    db = session_factory()
    try:
        return db.query(User).filter(User.id == user_id).first().token_version or 0
    finally:
        db.close()


def test_change_password_revokes_old_token(client, session_factory):
    """改密码后：旧 token 必须失效，新下发的 token 可用"""
    user_id, old_token, headers = make_user(session_factory, "chpwd_s16")

    # 前提：改密之前旧 token 可用（证明后面的 401 是改密造成的）
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert _token_version(session_factory, user_id) == 0

    resp = client.put("/api/v1/auth/password", json={
        "old_password": DEFAULT_PASSWORD,
        "new_password": "NewPassw0rd!456",
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    new_token = resp.json()["data"]["access_token"]

    # token_version 自增，旧 token 立刻被拒
    assert _token_version(session_factory, user_id) == 1
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {old_token}"}).status_code == 401

    # 换发的新 token（ver 已更新）继续可用，用户不会被自己踢下线
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {new_token}"}).status_code == 200

    # 旧 token 重新登录也不行 —— 密码确实换成了新的
    # （注意：/auth/login 内部用真实 SessionLocal，测试库里的用户它看不到，
    #   所以这里直接校验库中的 password_hash）
    db = session_factory()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        assert verify_password("NewPassw0rd!456", user.password_hash)
        assert not verify_password(DEFAULT_PASSWORD, user.password_hash)
    finally:
        db.close()


def test_reset_password_revokes_old_token(client, session_factory):
    """重置密码后：旧 token 必须失效（忘记密码场景同样要踢掉旧会话）"""
    user_id, old_token, headers = make_user(session_factory, "rstpwd_s16")

    db = session_factory()
    try:
        db.add(VerificationCode(
            email="rstpwd_s16@test.local",
            code="654321",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            attempts=0,
        ))
        db.commit()
    finally:
        db.close()

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    resp = client.post("/api/v1/auth/reset-password", json={
        "email": "rstpwd_s16@test.local",
        "code": "654321",
        "new_password": "ResetPassw0rd!789",
    })
    assert resp.status_code == 200, resp.text

    assert _token_version(session_factory, user_id) == 1
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {old_token}"}).status_code == 401


def test_other_user_token_unaffected_by_password_change(client, session_factory):
    """边界场景：改密码只吊销自己的 token，不能误伤其他用户"""
    _, other_token, other_h = make_user(session_factory, "other_s16")
    _, _, headers = make_user(session_factory, "chpwd_s16b")

    assert client.put("/api/v1/auth/password", json={
        "old_password": DEFAULT_PASSWORD,
        "new_password": "AnotherPass!321",
    }, headers=headers).status_code == 200

    assert client.get("/api/v1/auth/me", headers=other_h).status_code == 200
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {other_token}"}).status_code == 200


# ---------------- WebSocket 与 REST 共用同一条吊销逻辑 ----------------

def test_ws_rejects_token_with_stale_version(client, session_factory):
    """攻击复现：改密码后拿旧 token 重连 WebSocket

    WS 端点也必须走 resolve_user_from_token，否则「改密码吊销」只对 REST 生效，
    攻击者仍能凭旧 token 保持会议连接。
    """
    host_id, host_token, host_h = make_user(session_factory, "host_s16")
    data = create_meeting(client, host_h, title="令牌吊销验证")
    meeting_no = data["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, "host_s16")

    # 前提：吊销之前 WS 能正常建立
    with client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={pid}"
    ) as ws:
        ws.send_json({"type": "auth", "token": host_token})
        assert ws.receive_json()  # 至少能收到握手期消息

    # 模拟「用户改了密码」：token_version 自增，旧 token 的 ver 随即过期
    db = session_factory()
    try:
        db.query(User).filter(User.id == host_id).update(
            {User.token_version: 1}, synchronize_session=False)
        db.commit()
    finally:
        db.close()

    # 首帧鉴权后服务端立即断开（连接已 accept，故表现为 WebSocketDisconnect）
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/ws/{meeting_no}?participant_id={pid}"
        ) as ws:
            ws.send_json({"type": "auth", "token": host_token})
            for _ in range(3):
                ws.receive_json()


def test_ws_accepts_token_with_current_version(client, session_factory):
    """负向对照：ver 与库中一致的 token 必须能连上（防止校验写成永远拒绝）"""
    _, _, host_h = make_user(session_factory, "host_s16b")

    db = session_factory()
    try:
        user = db.query(User).filter(User.username == "host_s16b").first()
        token = create_access_token(token_claims(user))
    finally:
        db.close()

    data = create_meeting(client, host_h, title="令牌有效验证")
    meeting_no = data["meeting_no"]
    pid = participant_id_of(session_factory, meeting_no, "host_s16b")

    with client.websocket_connect(
        f"/api/v1/ws/{meeting_no}?participant_id={pid}"
    ) as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json() is not None