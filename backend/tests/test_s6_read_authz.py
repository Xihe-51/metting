"""
S6 修复验证：会议读侧接口的越权防护（会议隔离）

修复点（app/routers/meeting_router.py）：
- get_meeting / get_messages / list_recordings 增加 is_meeting_member 校验；
- list_meetings 不再向任意登录用户返回全站会议号清单。

运行：
    cd D:\\meeting\\backend
    D:\\conda\\envs\\meeting\\python.exe -m pytest tests/test_s6_read_authz.py -v
"""
from conftest import create_meeting, join_meeting, make_user  # noqa: E402


# ---------------- 攻击复现 ----------------

def test_outsider_cannot_read_meeting_chat_history(client, session_factory):
    """攻击复现：任意登录用户凭会议号读取他人会议群聊历史

    修复前：GET /meetings/{no}/messages -> 200（泄露全部群聊内容）
    修复后：-> 403
    """
    _, _, host_headers = make_user(session_factory, "s6_host")
    meeting = create_meeting(client, host_headers, title="机密评审会")

    _, _, attacker_headers = make_user(session_factory, "s6_attacker")
    resp = client.get(f"/api/v1/meetings/{meeting['meeting_no']}/messages", headers=attacker_headers)

    assert resp.status_code == 403, f"非参会人员不得读取群聊历史, got {resp.status_code}: {resp.text}"
    assert resp.json()["message"] == "无权查看该会议聊天记录"


def test_outsider_cannot_read_meeting_detail_and_roster(client, session_factory):
    """攻击复现：非参会人员读取会议详情（标题/参会名单/公告/锁定状态）"""
    _, _, host_headers = make_user(session_factory, "s6_host2")
    meeting = create_meeting(client, host_headers, title="薪酬方案会")

    _, _, attacker_headers = make_user(session_factory, "s6_attacker2")
    resp = client.get(f"/api/v1/meetings/{meeting['meeting_no']}", headers=attacker_headers)

    assert resp.status_code == 403
    assert resp.json()["message"] == "无权查看该会议"


def test_outsider_cannot_list_recordings(client, session_factory):
    """攻击复现：非参会人员枚举他人会议录制清单"""
    _, _, host_headers = make_user(session_factory, "s6_host3")
    meeting = create_meeting(client, host_headers, title="录制会议")

    _, _, attacker_headers = make_user(session_factory, "s6_attacker3")
    resp = client.get(f"/api/v1/meetings/{meeting['meeting_no']}/recordings", headers=attacker_headers)

    assert resp.status_code == 403
    assert resp.json()["message"] == "无权查看该会议录制"


def test_meeting_list_does_not_leak_others_meeting_numbers(client, session_factory):
    """攻击复现：/meetings 全站会议号泄露（S6 攻击链的第一步）"""
    _, _, host_headers = make_user(session_factory, "s6_host4")
    secret = create_meeting(client, host_headers, title="不该被看到的会议")

    _, _, attacker_headers = make_user(session_factory, "s6_attacker4")
    resp = client.get("/api/v1/meetings", headers=attacker_headers)

    assert resp.status_code == 200
    data = resp.json()["data"]
    leaked = [m["meeting_no"] for m in data["ongoing"] + data["scheduled"]]
    assert secret["meeting_no"] not in leaked, "不得泄露与当前用户无关的会议号"


# ---------------- 正常场景 ----------------

def test_creator_and_member_can_read_meeting_content(client, session_factory):
    """正常场景：创建者与已加入成员可正常访问会议内容"""
    _, _, host_headers = make_user(session_factory, "s6_ok_host")
    meeting = create_meeting(client, host_headers, title="正常会议")
    no = meeting["meeting_no"]

    _, _, member_headers = make_user(session_factory, "s6_ok_member")
    join_meeting(client, member_headers, no)

    for headers, who in ((host_headers, "创建者"), (member_headers, "参会成员")):
        assert client.get(f"/api/v1/meetings/{no}", headers=headers).status_code == 200, who
        assert client.get(f"/api/v1/meetings/{no}/messages", headers=headers).status_code == 200, who
        assert client.get(f"/api/v1/meetings/{no}/recordings", headers=headers).status_code == 200, who

    # 自己的会议必须出现在自己的会议列表里
    mine = client.get("/api/v1/meetings", headers=host_headers).json()["data"]
    assert no in [m["meeting_no"] for m in mine["ongoing"]]


def test_member_can_still_read_own_meeting_list(client, session_factory):
    """正常场景：成员通过 /meetings 能看到自己参与的会议"""
    _, _, host_headers = make_user(session_factory, "s6_list_host")
    meeting = create_meeting(client, host_headers, title="成员可见会议")

    _, _, member_headers = make_user(session_factory, "s6_list_member")
    join_meeting(client, member_headers, meeting["meeting_no"])

    mine = client.get("/api/v1/meetings", headers=member_headers).json()["data"]
    assert meeting["meeting_no"] in [m["meeting_no"] for m in mine["ongoing"]]


# ---------------- 异常边界 ----------------

def test_unauthenticated_request_rejected(client, session_factory):
    """异常边界：无 Token 一律 401"""
    _, _, host_headers = make_user(session_factory, "s6_noauth_host")
    no = create_meeting(client, host_headers, title="鉴权会议")["meeting_no"]

    assert client.get(f"/api/v1/meetings/{no}").status_code == 401
    assert client.get(f"/api/v1/meetings/{no}/messages").status_code == 401
    assert client.get(f"/api/v1/meetings/{no}/recordings").status_code == 401


def test_nonexistent_meeting_returns_404_before_authz(client, session_factory):
    """异常边界：会议不存在时返回 404（不因鉴权改造而改变语义）"""
    _, _, headers = make_user(session_factory, "s6_404")

    assert client.get("/api/v1/meetings/000000", headers=headers).status_code == 404
    assert client.get("/api/v1/meetings/000000/messages", headers=headers).status_code == 404
    assert client.get("/api/v1/meetings/000000/recordings", headers=headers).status_code == 404


def test_invalid_token_is_treated_as_outsider(client, session_factory):
    """异常边界：伪造/过期 Token 不得被视为会议成员"""
    _, _, host_headers = make_user(session_factory, "s6_badtoken_host")
    no = create_meeting(client, host_headers, title="伪造Token会议")["meeting_no"]

    bad = {"Authorization": "Bearer not-a-real-jwt"}
    assert client.get(f"/api/v1/meetings/{no}", headers=bad).status_code == 401
    assert client.get(f"/api/v1/meetings/{no}/messages", headers=bad).status_code == 401


def test_left_participant_history_still_readable(client, session_factory):
    """边界：已离会的历史参与者仍属会议相关人员（可回看自己参与过的会议内容）"""
    _, _, host_headers = make_user(session_factory, "s6_left_host")
    no = create_meeting(client, host_headers, title="离会回看会议")["meeting_no"]

    _, _, member_headers = make_user(session_factory, "s6_left_member")
    join_meeting(client, member_headers, no)
    # 走 REST 结束会议，成员记录保留（left_at 为空但仍是 member）
    assert client.get(f"/api/v1/meetings/{no}/messages", headers=member_headers).status_code == 200