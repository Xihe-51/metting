"""
S12 修复验证：忘记密码接口不得回传验证码 + 验证码不可被暴力枚举

修复点（app/routers/auth_router.py）：
- send_code：响应体默认不回显 code，仅 AUTH_DEV_MODE=1 时回显；
- send_code：邮箱是否注册都返回相同文案，消除账号枚举；
- reset_password：同一验证码失败 MAX_CODE_ATTEMPTS(5) 次即作废。

运行：
    cd D:\\meeting\\backend
    D:\\conda\\envs\\meeting\\python.exe -m pytest tests/test_s12_code_leak.py -v
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from conftest import DEFAULT_PASSWORD, make_user  # noqa: E402
from app.models import User, VerificationCode  # noqa: E402
from app.routers.auth_router import verify_password  # noqa: E402

SEND_CODE = "/api/v1/auth/send-code"
RESET = "/api/v1/auth/reset-password"


def _user_row(session_factory, username):
    db = session_factory()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()


def _code_row(session_factory, email):
    db = session_factory()
    try:
        return db.query(VerificationCode).filter(
            VerificationCode.email == email
        ).order_by(VerificationCode.id.desc()).first()
    finally:
        db.close()


# ---------------- 正常场景 ----------------

def test_send_code_never_returns_code_in_response(client, session_factory, monkeypatch):
    """默认（生产）配置下：接口不回显验证码，但服务端确实生成了验证码"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_alice")

    resp = client.post(SEND_CODE, json={"email": "s12_alice@test.local"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 200, "统一响应体业务码应为 200"
    # 核心断言：响应体任何位置都不能出现验证码
    assert body.get("data") in (None, {}), f"响应不应携带数据: {body}"
    assert "code" not in (body.get("data") or {})

    row = _code_row(session_factory, "s12_alice@test.local")
    assert row is not None, "服务端应已入库验证码"
    assert len(row.code) == 6 and row.code.isdigit()


def test_reset_password_success_with_server_side_code(client, session_factory, monkeypatch):
    """正常找回密码：拿到（服务端下发的）验证码后可成功重置"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_bob")

    client.post(SEND_CODE, json={"email": "s12_bob@test.local"})
    real_code = _code_row(session_factory, "s12_bob@test.local").code

    resp = client.post(RESET, json={
        "email": "s12_bob@test.local", "code": real_code, "new_password": "NewPassw0rd!"
    })

    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "密码重置成功"
    assert verify_password("NewPassw0rd!", _user_row(session_factory, "s12_bob").password_hash)
    assert _code_row(session_factory, "s12_bob@test.local") is None, "验证码用后即焚"


def test_dev_mode_can_echo_code_for_local_debug(client, session_factory, monkeypatch):
    """仅显式开启 AUTH_DEV_MODE 时才回显，保证本地联调/自动化可用"""
    monkeypatch.setenv("AUTH_DEV_MODE", "1")
    make_user(session_factory, "s12_dev")

    resp = client.post(SEND_CODE, json={"email": "s12_dev@test.local"})

    assert resp.status_code == 200
    code = resp.json()["data"]["code"]
    assert len(code) == 6


# ---------------- 攻击复现 ----------------

def test_attack_without_code_cannot_takeover_account(client, session_factory, monkeypatch):
    """攻击复现：修复前攻击者可从响应体直接拿到验证码完成接管；修复后拿不到

    修复前：
        POST /send-code -> {"data": {"code": "123456"}}  -> 直接改掉受害者密码
    修复后：
        POST /send-code -> data 为 None；用猜测码重置一律 400，密码不变
    """
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    _, _, _ = make_user(session_factory, "s12_victim")
    original_hash = _user_row(session_factory, "s12_victim").password_hash

    # 1) 攻击者请求验证码，尝试窃取
    resp = client.post(SEND_CODE, json={"email": "s12_victim@test.local"})
    assert resp.status_code == 200
    leaked = (resp.json().get("data") or {})
    assert "code" not in leaked, "验证码不得出现在响应体中"

    # 2) 用任意猜测码重置 -> 必须失败
    for guess in ("000000", "123456", "999999"):
        r = client.post(RESET, json={
            "email": "s12_victim@test.local", "code": guess, "new_password": "Hacked!"
        })
        assert r.status_code == 400, f"猜测码 {guess} 不应通过"

    assert _user_row(session_factory, "s12_victim").password_hash == original_hash, "密码不得被篡改"


def test_send_code_no_account_enumeration(client, session_factory, monkeypatch):
    """异常/边界：已注册与未注册邮箱的响应必须完全一致，避免枚举账号"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_exists")

    r_exist = client.post(SEND_CODE, json={"email": "s12_exists@test.local"})
    r_absent = client.post(SEND_CODE, json={"email": "nobody@test.local"})

    assert r_exist.status_code == r_absent.status_code == 200
    assert r_exist.json()["message"] == r_absent.json()["message"]
    assert r_exist.json()["data"] == r_absent.json()["data"] is None


# ---------------- 异常边界 ----------------

def test_expired_code_rejected(client, session_factory, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_expired")

    db = session_factory()
    try:
        db.add(VerificationCode(
            email="s12_expired@test.local", code="111222",
            expires_at=datetime.utcnow() - timedelta(minutes=1), attempts=0,
        ))
        db.commit()
    finally:
        db.close()

    resp = client.post(RESET, json={
        "email": "s12_expired@test.local", "code": "111222", "new_password": "NewPassw0rd!"
    })
    assert resp.status_code == 400
    assert resp.json()["message"] == "验证码已过期"


def test_code_invalidated_after_max_attempts(client, session_factory, monkeypatch):
    """边界：连续 5 次错误后验证码作废，之后即使输入正确验证码也不再可用"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_limit")
    client.post(SEND_CODE, json={"email": "s12_limit@test.local"})
    real_code = _code_row(session_factory, "s12_limit@test.local").code

    for i in range(5):
        r = client.post(RESET, json={
            "email": "s12_limit@test.local", "code": "000000", "new_password": "NewPassw0rd!"
        })
        assert r.status_code == 400, f"第 {i + 1} 次错误尝试不应通过"

    assert _code_row(session_factory, "s12_limit@test.local") is None, "超限后验证码应被删除"

    r = client.post(RESET, json={
        "email": "s12_limit@test.local", "code": real_code, "new_password": "NewPassw0rd!"
    })
    assert r.status_code == 400, "已作废的验证码不得再通过"


# ---------------- 并发场景 ----------------

def test_concurrent_brute_force_cannot_reset_password(client, session_factory, monkeypatch):
    """并发暴力破解：20 路并发猜测验证码，必须全部失败且密码不变"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_brute")
    original_hash = _user_row(session_factory, "s12_brute").password_hash
    client.post(SEND_CODE, json={"email": "s12_brute@test.local"})

    def attempt(i):
        return client.post(RESET, json={
            "email": "s12_brute@test.local",
            "code": f"{i:06d}",
            "new_password": "Hacked!",
        }).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(attempt, range(20)))

    assert 200 not in statuses, f"并发暴力破解不得有任何一次成功: {statuses}"
    assert _user_row(session_factory, "s12_brute").password_hash == original_hash


def test_concurrent_reset_with_same_code_only_succeeds_once(client, session_factory, monkeypatch):
    """并发场景：同一个有效验证码被多路同时提交，最多只能成功一次（单次使用）"""
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    make_user(session_factory, "s12_once")
    client.post(SEND_CODE, json={"email": "s12_once@test.local"})
    real_code = _code_row(session_factory, "s12_once@test.local").code

    def attempt(_):
        return client.post(RESET, json={
            "email": "s12_once@test.local", "code": real_code, "new_password": "NewPassw0rd!"
        }).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(8)))

    assert statuses.count(200) == 1, f"同一验证码只允许成功一次: {statuses}"
    assert _code_row(session_factory, "s12_once@test.local") is None, "验证码应已被占用删除"