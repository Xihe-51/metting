"""
S11 录制分片乱序并发上传 —— 复现脚本 + 修复后验证

【修复前的问题】
前端 `mediaRecorder.ondataavailable` 每 1 秒产出一片并**并发**上传（uploadChunk
不等待前一片），后端 `upload_recording_chunk` 直接以 `open(..., "ab")` 按
「HTTP 到达顺序」追加。网络抖动会让后生成的分片先落盘，webm 的 EBML / Cluster
结构被破坏 —— 录下来的文件无法播放。分片之间没有任何顺序标识，服务端无从纠正。

【修复要点】
- 前端为每片附带 `X-Chunk-Seq` 序号（序号顺序 = 录制时序）；
- 后端 `_append_chunk` 按序号重排：seq==expected 立即落盘并顺带冲刷
  缓冲区中连续的分片，seq>expected 先缓存，seq<expected 视为重复丢弃；
- 停止录制时 `_close_recording` 冲刷缓冲区，尽量保住尾部数据；
- 配额校验与落盘在同一把锁内完成（并发分片不能一起“看到还有空间”后超配额落盘），
  且停止录制先收口（拒绝新分片）再冲刷、最后才置 completed，停止后不再有字节落盘。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s11_recording_chunk_order.py -v
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import create_meeting, make_user  # noqa: E402
from app.routers import meeting_router  # noqa: E402

REC_DIR = Path("recordings")


def _chunk(tag: bytes, times: int = 8) -> bytes:
    return tag * times


CHUNKS = {0: _chunk(b"A"), 1: _chunk(b"B"), 2: _chunk(b"C")}
LOGICAL = CHUNKS[0] + CHUNKS[1] + CHUNKS[2]


# ---------------- 辅助 ----------------

def _setup_recording(client, session_factory, name):
    """建会议 + 开始录制，返回 (meeting_no, recording_id, file_name, headers)"""
    _, _, headers = make_user(session_factory, name)
    meeting_no = create_meeting(client, headers, title="录制分片排序")["meeting_no"]
    resp = client.post(f"/api/v1/meetings/{meeting_no}/recordings/start", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return meeting_no, data["recording_id"], data["file_name"], headers


def _post_chunk(client, meeting_no, recording_id, headers, data: bytes, seq=None):
    h = dict(headers)
    if seq is not None:
        h["X-Chunk-Seq"] = str(seq)
    return client.post(
        f"/api/v1/meetings/{meeting_no}/recordings/{recording_id}/chunk",
        content=data, headers=h)


def _read(file_name: str) -> bytes:
    return (REC_DIR / file_name).read_bytes()


def _cleanup(file_name: str):
    try:
        (REC_DIR / file_name).unlink()
    except FileNotFoundError:
        pass


# ---------------- 复现 + 修复后验证 ----------------

def test_negative_control_arrival_order_append_corrupts_recording(client, session_factory):
    """对照：不带序号时后端只能按到达顺序追加 —— 乱序到达即产生损坏的字节序"""
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11a")
    try:
        # 模拟并发上传时的乱序到达：2 号先到、0 号最后到
        for seq in (2, 0, 1):
            assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[seq]).status_code == 200
        got = _read(file_name)
    finally:
        _cleanup(file_name)

    assert got == CHUNKS[2] + CHUNKS[0] + CHUNKS[1], "旧行为就是按 HTTP 到达顺序落盘"
    assert got != LOGICAL, "乱序到达时字节序与录制时序不一致 —— 这正是 webm 损坏的成因"


def test_chunks_reordered_by_seq(client, session_factory):
    """修复后：乱序到达的分片按序号重排落盘，文件字节序与录制时序一致"""
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11b")
    try:
        for seq in (2, 0, 1):
            resp = _post_chunk(client, meeting_no, rid, headers, CHUNKS[seq], seq=seq)
            assert resp.status_code == 200, resp.text
        got = _read(file_name)
    finally:
        _cleanup(file_name)

    assert got == LOGICAL


def test_concurrent_chunks_land_in_recording_order(client, session_factory):
    """修复后：6 个分片并发上传（到达顺序完全打乱），落盘顺序仍严格递增"""
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11c")
    arrival = [5, 0, 3, 1, 4, 2]
    payloads = {i: _chunk(bytes([65 + i])) for i in range(6)}
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_post_chunk, client, meeting_no, rid, headers, payloads[i], i)
                       for i in arrival]
            for f in futures:
                assert f.result().status_code == 200
        got = _read(file_name)
    finally:
        _cleanup(file_name)

    assert got == b"".join(payloads[i] for i in range(6))


def test_duplicate_chunk_is_discarded(client, session_factory):
    """修复后：重复到达的同一序号分片被丢弃，不会把内容写两遍"""
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11d")
    try:
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[0], seq=0).status_code == 200
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[0], seq=0).status_code == 200
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[1], seq=1).status_code == 200
        got = _read(file_name)
    finally:
        _cleanup(file_name)

    assert got == CHUNKS[0] + CHUNKS[1]


def test_pending_chunks_flushed_on_stop(client, session_factory):
    """修复后：中间分片彻底丢失时，停止录制会冲刷缓冲区，不丢尾部数据"""
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11e")
    try:
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[0], seq=0).status_code == 200
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[2], seq=2).status_code == 200
        assert _read(file_name) == CHUNKS[0], "1 号分片未到达，2 号分片应停在缓冲区而不是先落盘"

        stop = client.post(f"/api/v1/meetings/{meeting_no}/recordings/{rid}/stop", headers=headers)
        assert stop.status_code == 200, stop.text
        got = _read(file_name)
    finally:
        _cleanup(file_name)

    assert got == CHUNKS[0] + CHUNKS[2]


# ---------------- 写入收口与配额原子性（回归） ----------------

QUOTA = {"max_chunk_bytes": 1024, "max_total_bytes": 4096, "max_storage_bytes": 8192}


@pytest.fixture()
def quota_env(tmp_path, monkeypatch):
    """把录制目录与配额都改到临时目录 + 极小阈值，便于快速触发单场配额"""
    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    monkeypatch.setattr(meeting_router, "RECORDINGS_DIR", str(rec_dir))
    monkeypatch.setattr(meeting_router, "RECORDING_QUOTA", dict(QUOTA))
    return rec_dir


def test_concurrent_legacy_chunks_cannot_exceed_meeting_quota(client, session_factory, quota_env):
    """回归：8 个不带序号的分片并发上传（旧客户端路径原先既不持锁、配额校验也在锁外）

    修复前这条路径直接 open 追加且各请求先在锁外读文件大小，并发下会一起通过校验、
    集体超配额落盘；修复后校验与写入同锁，最多 4 片（4KB）通过，其余全部 413。
    """
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11_quota")
    size = QUOTA["max_chunk_bytes"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(
            lambda _: _post_chunk(client, meeting_no, rid, headers, b"Q" * size).status_code,
            range(8)))

    on_disk = (quota_env / file_name).stat().st_size if (quota_env / file_name).exists() else 0
    assert statuses.count(200) == QUOTA["max_total_bytes"] // size, f"并发下放行数异常: {statuses}"
    assert on_disk == QUOTA["max_total_bytes"], f"并发落盘突破了单场配额: {on_disk} 字节"
    assert set(statuses) <= {200, 413}, f"非预期状态码: {statuses}"


def test_buffered_chunks_count_toward_quota(client, session_factory, quota_env):
    """回归：乱序分片停在缓冲区时也占用配额，不能「先缓冲 4KB 再直写」绕过单场上限

    修复前配额只看磁盘文件大小，缓冲区里的分片等于隐身；修复后
    「已接受字节 = 磁盘 + 缓冲区」，缓冲 4 片后第 5 片必须 413。
    """
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11_buf")
    size = QUOTA["max_chunk_bytes"]

    # 0 号分片始终不发：1~4 号全部停在缓冲区（文件被创建但 0 字节）
    for seq in range(1, 5):
        assert _post_chunk(client, meeting_no, rid, headers, b"B" * size, seq=seq).status_code == 200
    assert (quota_env / file_name).stat().st_size == 0, "乱序分片应停在缓冲区而不是先落盘"

    overflow = _post_chunk(client, meeting_no, rid, headers, b"B" * size, seq=5)
    assert overflow.status_code == 413, overflow.text


def test_in_flight_chunk_cannot_write_after_stop(client, session_factory):
    """竞态回归：写入晚于停止（收口）的分片必须被拒 —— 停止后再无字节落盘

    停止流程是「登记收口（_close_recording）→ 冲刷缓冲 → 置 completed」。
    已通过「录制进行中」检查、却晚于停止请求到达写入点的在途分片，会在收口登记处
    被拦下；修复前没有收口概念，这类分片会在冲刷之后继续 append，把收尾后的文件写乱。
    """
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11_race")
    try:
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[0], seq=0).status_code == 200

        stop = client.post(f"/api/v1/meetings/{meeting_no}/recordings/{rid}/stop", headers=headers)
        assert stop.status_code == 200, stop.text
        baseline = _read(file_name)

        # 模拟「停止前已通过状态检查、停止后才走到写入」的在途分片（带序号 / 不带序号两条路径）
        file_path = str(REC_DIR / file_name)
        blocked_seq = meeting_router._append_chunk(rid, file_path, 1, CHUNKS[1])
        blocked_legacy = meeting_router._append_chunk(rid, file_path, None, CHUNKS[2])

        assert blocked_seq is not None and blocked_seq[0] == 400, "收口后的写入必须被拒绝"
        assert blocked_legacy is not None and blocked_legacy[0] == 400, "收口后的写入必须被拒绝"
        assert _read(file_name) == baseline, "停止之后不得再有任何字节落盘"
    finally:
        _cleanup(file_name)


def test_chunk_after_stop_is_rejected(client, session_factory):
    """停止录制后：带序号与不带序号的分片都返回 400，文件不再增长"""
    meeting_no, rid, file_name, headers = _setup_recording(client, session_factory, "host_s11_stop")
    try:
        assert _post_chunk(client, meeting_no, rid, headers, CHUNKS[0], seq=0).status_code == 200
        stop = client.post(f"/api/v1/meetings/{meeting_no}/recordings/{rid}/stop", headers=headers)
        assert stop.status_code == 200, stop.text
        before = _read(file_name)

        late_seq = _post_chunk(client, meeting_no, rid, headers, CHUNKS[1], seq=1)
        late_legacy = _post_chunk(client, meeting_no, rid, headers, CHUNKS[1])
        assert late_seq.status_code == 400 and late_legacy.status_code == 400
        assert _read(file_name) == before
    finally:
        _cleanup(file_name)