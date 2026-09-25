from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from conftest import create_meeting, get_meeting_row, make_user
from app.models import Participant
from app.routers import meeting_router


def test_join_races_for_last_participant_slot(client, session_factory, monkeypatch):
    monkeypatch.setenv("MEETING_MAX_PARTICIPANTS", "4")
    _, _, host_headers = make_user(session_factory, "review_cap_host")
    meeting_no = create_meeting(client, host_headers)["meeting_no"]
    joiners = [make_user(session_factory, f"review_cap_{i}")[2] for i in range(8)]
    barrier = Barrier(len(joiners))

    def attempt(headers):
        barrier.wait()
        return client.post(
            f"/api/v1/meetings/{meeting_no}/join",
            json={"password": ""},
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=len(joiners)) as pool:
        responses = list(pool.map(attempt, joiners))

    assert sum(response.status_code == 200 for response in responses) == 3
    assert sum(response.status_code == 409 for response in responses) == 5
    assert all(response.status_code in (200, 409) for response in responses)
    assert get_meeting_row(session_factory, meeting_no).participant_count == 4


def test_video_seat_check_and_state_update_have_no_await_gap():
    source = Path(meeting_router.__file__).read_text(encoding="utf-8")
    start = source.index('            elif message_type == "device":')
    end = source.index('            elif message_type == "screen":', start)
    device_branch = source[start:end]
    check_at = device_branch.index("seat_used = manager.count_video_on(meeting_no)")
    denied_block_start = device_branch.index('"type": "video_denied"', check_at)
    denied_block_end = device_branch.index("continue", denied_block_start)
    denied_check_path = device_branch[check_at:device_branch.index("if seat_used >= seat_limit", check_at)]
    denied_block = device_branch[denied_block_start:denied_block_end]
    update_path = device_branch[device_branch.index("manager.update_participant_status(", check_at):]
    assert "await " not in denied_check_path
    assert "await " not in update_path.split("await manager.broadcast_to_meeting", 1)[0]
    assert '"type": "video_denied"' in denied_block


def test_concurrent_join_count_matches_active_participant_rows(client, session_factory):
    _, _, host_headers = make_user(session_factory, "review_count_host")
    meeting_no = create_meeting(client, host_headers)["meeting_no"]
    joiners = [make_user(session_factory, f"review_count_{i}")[2] for i in range(12)]
    barrier = Barrier(len(joiners))

    def attempt(headers):
        barrier.wait()
        return client.post(
            f"/api/v1/meetings/{meeting_no}/join",
            json={"password": ""},
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=len(joiners)) as pool:
        responses = list(pool.map(attempt, joiners))

    assert all(response.status_code == 200 for response in responses)
    meeting = get_meeting_row(session_factory, meeting_no)
    db = session_factory()
    try:
        active_count = db.query(Participant).filter(
            Participant.meeting_id == meeting.id,
            Participant.left_at.is_(None),
            Participant.status.in_(("joined", "waiting")),
        ).count()
    finally:
        db.close()
    assert meeting.participant_count == active_count == len(joiners) + 1
