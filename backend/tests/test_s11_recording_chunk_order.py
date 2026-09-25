"""
S11 录制分片乱序并发上传 —— 复现脚本 + 修复后验证

【修复前的问题】
前端 `mediaRecorder.ondataavailable` 每 1 秒产出一片并**并发**上传（uploadChunk
不等待前一片），后端 `upload_recording_chunk` 直接以 `open(..., "ab")` 按
「HTTP 到达顺序」追加。网络抖动会让后生成的分片先落盘，webm 的 EBML / Cluster
结构被破坏 —— 录下来的文件无法播放。分片之间没有任何顺序标识，服务端无从纠正。

【修复要点】
- 前端为每片附带 `X-Chunk-Seq` 序号（序号顺序 = 录制时序）；
- 后端 `_write_chunk_in_order` 按序号重排：seq==expected 立即落盘并顺带冲刷
  缓冲区中连续的分片，seq>expected 先缓存，seq<expected 视为重复丢弃；
- 停止录制时 `_flush_pending_chunks` 冲刷缓冲区，尽量保住尾部数据。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s11_recording_chunk_order.py -v
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conftest import create_meeting, make_user  # noqa: E402

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