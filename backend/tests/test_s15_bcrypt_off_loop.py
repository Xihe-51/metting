"""
S15 bcrypt 阻塞事件循环 —— 复现脚本 + 修复后验证

【修复前的问题】
bcrypt 是 CPU 密集的同步调用（单次数百毫秒，且随 cost 增长）。`create_meeting`
（会议密码哈希）与 `join_meeting`（会议密码校验）都是 `async def`，却在事件循环
里**同步**执行 bcrypt：
    password_hash = get_password_hash(request.password)      # create_meeting
    verify_password(request.password, meeting.password_hash) # join_meeting
事件循环被占满后，同一进程内的其它请求（登录、心跳、任何 API）全部排队，
并发入会/建会即可把整个后端拖到不可用。

【修复要点】
把这两个 bcrypt 调用交给 `run_in_threadpool` 执行，事件循环在等待期间可继续
处理其它请求（auth_router 的登录接口此前已是这种写法）。

复现手法：把 `verify_password` 换成一个「先置位事件、再 sleep」的慢函数；
    - `run_in_threadpool` 退化为「直接在事件循环里执行」= 修复前行为；
    - 保持 `run_in_threadpool` 原样 = 修复后行为。
两种情况下都去测量「另一个无关请求」的响应耗时：修复前会被整体阻塞。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s15_bcrypt_off_loop.py -v
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import create_meeting, make_user  # noqa: E402
from app.routers import meeting_router  # noqa: E402

SLOW = 1.0          # 慢速 bcrypt 的模拟耗时（秒）
FAST_PATH = "/api/v1/meetings/my"


def _install_slow_verify(monkeypatch, inline: bool) -> threading.Event:
    """把会议密码校验换成慢速版本；inline=True 时复现「bcrypt 跑在事件循环里」"""
    started = threading.Event()

    def slow_verify(plain_password, hashed_password):
        started.set()
        time.sleep(SLOW)
        return True

    monkeypatch.setattr(meeting_router, "verify_password", slow_verify)
    if inline:
        async def _inline(func, *args, **kwargs):
            return func(*args, **kwargs)   # 直接在事件循环里执行，等价于修复前的写法
        monkeypatch.setattr(meeting_router, "run_in_threadpool", _inline)
    return started


def _time_unrelated_request(client, headers, started) -> float:
    """等慢速校验开始后，测量一个无关请求的耗时（即事件循环被占用的时间）"""
    assert started.wait(timeout=5.0), "慢速 bcrypt 未被触发，用例前提不成立"
    t0 = time.monotonic()
    resp = client.get(FAST_PATH, headers=headers)
    elapsed = time.monotonic() - t0
    assert resp.status_code == 200, resp.text
    return elapsed


def test_negative_control_inline_bcrypt_blocks_event_loop(client, session_factory, monkeypatch):
    """对照：bcrypt 直接跑在事件循环里时，无关请求会被整体阻塞"""
    _, _, host_h = make_user(session_factory, "host_s15a")
    _, _, guest_h = make_user(session_factory, "guest_s15a")
    meeting_no = create_meeting(client, host_h, title="慢校验", password="Secret!123")["meeting_no"]

    started = _install_slow_verify(monkeypatch, inline=True)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(client.post, f"/api/v1/meetings/{meeting_no}/join",
                          json={"password": "Secret!123"}, headers=guest_h)
        blocked = _time_unrelated_request(client, host_h, started)
        assert fut.result().status_code == 200

    assert blocked >= SLOW * 0.8, \
        f"bcrypt 在事件循环里执行时，无关请求应被阻塞约 {SLOW}s，实测仅 {blocked:.2f}s"


def test_join_password_verify_runs_off_event_loop(client, session_factory, monkeypatch):
    """修复后：入会时的密码校验在线程池执行，事件循环照常服务其它请求"""
    _, _, host_h = make_user(session_factory, "host_s15b")
    _, _, guest_h = make_user(session_factory, "guest_s15b")
    meeting_no = create_meeting(client, host_h, title="慢校验", password="Secret!123")["meeting_no"]

    started = _install_slow_verify(monkeypatch, inline=False)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(client.post, f"/api/v1/meetings/{meeting_no}/join",
                          json={"password": "Secret!123"}, headers=guest_h)
        served = _time_unrelated_request(client, host_h, started)
        assert fut.result().status_code == 200, "慢速校验返回 True，入会应成功"

    assert served < SLOW * 0.5, \
        f"密码校验在线程池执行时，无关请求不应被阻塞，实测 {served:.2f}s"


def test_create_meeting_password_hash_runs_off_event_loop(client, session_factory, monkeypatch):
    """修复后：创建会议时的密码哈希同样在线程池执行，不阻塞事件循环"""
    _, _, host_h = make_user(session_factory, "host_s15c")
    started = threading.Event()

    def slow_hash(password):
        started.set()
        time.sleep(SLOW)
        return "x" * 60   # 非真实 bcrypt 摘要，用例只关心是否阻塞事件循环

    monkeypatch.setattr(meeting_router, "get_password_hash", slow_hash)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(client.post, "/api/v1/meetings", json={
            "title": "慢哈希", "password": "Secret!123", "scheduled_at": None,
            "waiting_room": False, "whitelist": False,
        }, headers=host_h)
        served = _time_unrelated_request(client, host_h, started)
        assert fut.result().status_code == 200

    assert served < SLOW * 0.5, \
        f"密码哈希在线程池执行时，无关请求不应被阻塞，实测 {served:.2f}s"