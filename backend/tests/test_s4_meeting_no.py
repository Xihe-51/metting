"""
S4 会议号生成 —— 复现脚本 + 修复后验证

【修复前的问题】
1. 弱随机：`random.randint(100000, 999999)` 用的是梅森旋转伪随机数，序列可预测 ——
   攻击者观察若干会议号后即可复现/推测后续号码，预判并抢占即将创建的会议。
2. 撞号：`generate_meeting_no` 是「先查后插」，两个并发创建可能选到同一号码，
   后提交者直接撞 meeting_no 唯一约束，把 500 抛给前端。
3. 可枚举：6 位号空间只有 90 万，而 /join 与会议信息查询没有任何频率限制，
   攻击者可遍历全部号码，筛出所有有效会议号。

【修复要点】
- `secrets.randbelow` 生成号码（加密安全随机，不受 random 种子影响）；
- `create_meeting` 捕获 IntegrityError 后换号重试（最多 MAX_MEETING_NO_RETRY 次）；
- `/join` 与 `GET /meetings/{no}` 加滑动窗口限流（app/rate_limit.py）。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s4_meeting_no.py -v
"""
import random

import pytest
from sqlalchemy.exc import IntegrityError

from conftest import create_meeting, make_user  # noqa: E402
from app import rate_limit  # noqa: E402
from app.models import Meeting  # noqa: E402
from app.routers import meeting_router  # noqa: E402
from app.routers.meeting_router import generate_meeting_no  # noqa: E402

CREATE_BODY = {"title": "撞号重试", "password": "", "scheduled_at": None,
               "waiting_room": False, "whitelist": False}


# ---------------- 问题 1：弱随机（号码可预测） ----------------

def _legacy_numbers(n=20):
    """修复前的写法：random.randint（可预测）"""
    return [str(random.randint(100000, 999999)) for _ in range(n)]


def test_negative_control_random_seed_reproduces_sequence():
    """对照：旧写法在固定 random 种子下序列完全一致 —— 号码可被攻击者复现"""
    random.seed(2024)
    first = _legacy_numbers()
    random.seed(2024)
    second = _legacy_numbers()
    assert first == second, "random 序列本应可复现"


def test_meeting_no_not_reproducible_even_if_random_seed_known(session_factory):
    """修复后：即便攻击者控制了 random 种子，也复现不出会议号序列"""
    db = session_factory()
    try:
        random.seed(2024)
        first = [generate_meeting_no(db) for _ in range(20)]
        random.seed(2024)
        second = [generate_meeting_no(db) for _ in range(20)]
    finally:
        db.close()

    assert first != second, "会议号仍由 random 驱动，可被预测"
    assert all(len(no) == 6 and no.isdigit() for no in first + second)


# ---------------- 问题 2：并发撞号（唯一约束冲突 → 500） ----------------

def test_negative_control_duplicate_meeting_no_raises_integrity_error(session_factory):
    """对照：号码撞唯一约束会抛 IntegrityError（旧代码不捕获 → 前端 500）"""
    db = session_factory()
    try:
        db.add(Meeting(meeting_no="300001", title="A", creator_id=1,
                       status="ongoing", participant_count=1))
        db.commit()

        db.add(Meeting(meeting_no="300001", title="B", creator_id=1,
                       status="ongoing", participant_count=1))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_create_meeting_retries_when_number_already_taken(client, session_factory, monkeypatch):
    """修复后：生成器第一次给出已被占用的号码，接口应换号重试而不是 500"""
    _, _, headers = make_user(session_factory, "creator_s4a")
    taken = create_meeting(client, headers, title="占号会议")["meeting_no"]

    calls = {"n": 0}
    real_generate = meeting_router.generate_meeting_no

    def fake_generate(db):
        calls["n"] += 1
        if calls["n"] == 1:
            return taken          # 第一次故意返回已被占用的号码
        return real_generate(db)

    monkeypatch.setattr(meeting_router, "generate_meeting_no", fake_generate)

    resp = client.post("/api/v1/meetings", json=CREATE_BODY, headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["meeting_no"] != taken
    assert calls["n"] == 2, "应在撞号后重试一次"


# ---------------- 问题 3：会议号可被枚举 ----------------

def test_negative_control_enumeration_unlimited_without_rate_limit(client, session_factory):
    """对照：没有频率限制时，攻击者可连续探测任意多个会议号且都被如实回答"""
    _, _, headers = make_user(session_factory, "enum_attacker_a")

    codes = []
    for i in range(40):
        rate_limit.reset()   # 模拟修复前「无限流」状态
        resp = client.post(f"/api/v1/meetings/{900000 + i}/join",
                           json={"password": ""}, headers=headers)
        codes.append(resp.status_code)

    assert codes == [404] * 40, codes


def test_join_rate_limit_throttles_enumeration(client, session_factory):
    """修复后：/join 达到配额即返回 429，号码遍历不可行"""
    _, _, headers = make_user(session_factory, "enum_attacker_b")
    rate_limit.reset()

    codes = []
    for i in range(meeting_router.JOIN_LIMIT_PER_USER + 15):
        resp = client.post(f"/api/v1/meetings/{910000 + i}/join",
                           json={"password": ""}, headers=headers)
        codes.append(resp.status_code)

    limit = meeting_router.JOIN_LIMIT_PER_USER
    assert codes[:limit] == [404] * limit, codes[:limit]
    assert codes[limit] == 429, codes[limit]
    assert all(c == 429 for c in codes[limit:]), codes[limit:]


def test_meeting_info_oracle_is_throttled(client, session_factory):
    """会议信息接口的 404/403 差异构成号码 oracle，必须限流

    这里刻意保留 404（不存在）与 403（存在但无权）的语义差异 —— 批次 1 的
    S6 越权防护就是这样定义的 —— 因此防护手段是限流而非修改状态码语义。
    """
    _, _, host_h = make_user(session_factory, "host_s4b")
    _, _, other_h = make_user(session_factory, "other_s4b")
    meeting_no = create_meeting(client, host_h, title="oracle 会议")["meeting_no"]

    # 真实会议 + 非成员 → 403（泄漏「存在」）
    assert client.get(f"/api/v1/meetings/{meeting_no}", headers=other_h).status_code == 403
    # 不存在的号码 → 404（泄漏「不存在」）—— 差异即可用于枚举
    assert client.get("/api/v1/meetings/990001", headers=other_h).status_code == 404

    rate_limit.reset()
    limit = meeting_router.MEETING_INFO_LIMIT_PER_USER
    codes = [client.get(f"/api/v1/meetings/{920000 + i}", headers=other_h).status_code
             for i in range(limit + 10)]

    assert codes[:limit] == [404] * limit
    assert codes[limit] == 429
    assert all(c == 429 for c in codes[limit:])