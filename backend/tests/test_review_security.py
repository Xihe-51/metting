from datetime import datetime, timedelta

from conftest import create_meeting, join_meeting, make_user, participant_id_of
from app.models import Meeting, Participant, Recording, VerificationCode, User


def _create_recording(session_factory, meeting_no, file_path):
    db = session_factory()
    try:
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        recording = Recording(
            meeting_id=meeting.id,
            file_name="review_security.webm",
            file_path=str(file_path),
            status="completed",
            file_size=0,
        )
        db.add(recording)
        db.commit()
        db.refresh(recording)
        return recording.id
    finally:
        db.close()


def test_nonmember_cannot_play_or_download_recording(client, session_factory, tmp_path):
    _, _, host_headers = make_user(session_factory, "review_recording_host")
    _, _, outsider_headers = make_user(session_factory, "review_recording_outsider")
    meeting_no = create_meeting(client, host_headers)["meeting_no"]
    file_path = tmp_path / "review_security.webm"
    file_path.write_bytes(b"webm")
    recording_id = _create_recording(session_factory, meeting_no, file_path)

    assert client.get(f"/api/v1/recordings/{recording_id}/play", headers=outsider_headers).status_code == 403
    assert client.get(f"/api/v1/recordings/{recording_id}/download", headers=outsider_headers).status_code == 403
    assert client.get(f"/api/v1/recordings/{recording_id}/play", headers=host_headers).status_code == 200
    assert client.get(f"/api/v1/recordings/{recording_id}/download", headers=host_headers).status_code == 200


def test_reset_password_without_valid_code_does_not_change_password(client, session_factory):
    user_id, old_token, old_headers = make_user(session_factory, "review_reset_user")
    old_password = "Passw0rd!123"
    db = session_factory()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        original_hash = user.password_hash
        db.add(VerificationCode(
            email=user.email,
            code="111111",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            attempts=0,
        ))
        db.commit()
        email = user.email
    finally:
        db.close()

    response = client.post("/api/v1/auth/reset-password", json={
        "email": email,
        "code": "222222",
        "new_password": "NewPassw0rd!456",
    })
    assert response.status_code == 400
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 200
    db = session_factory()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        assert user.password_hash == original_hash
        assert user.token_version == 0
    finally:
        db.close()
    assert old_token
    assert old_password


def test_reset_password_invalidates_old_token(client, session_factory):
    user_id, _, headers = make_user(session_factory, "review_reset_revokes")
    db = session_factory()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        email = user.email
        db.add(VerificationCode(
            email=email,
            code="654321",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            attempts=0,
        ))
        db.commit()
    finally:
        db.close()

    response = client.post("/api/v1/auth/reset-password", json={
        "email": email,
        "code": "654321",
        "new_password": "NewPassw0rd!456",
    })
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_kicked_user_cannot_rejoin(client, session_factory):
    _, host_token, host_headers = make_user(session_factory, "review_kick_host")
    _, _, guest_headers = make_user(session_factory, "review_kick_guest")
    meeting_no = create_meeting(client, host_headers)["meeting_no"]
    join_meeting(client, guest_headers, meeting_no)
    guest_id = participant_id_of(session_factory, meeting_no, "review_kick_guest")

    db = session_factory()
    try:
        guest = db.query(Participant).filter(Participant.id == guest_id).first()
        guest.status = "kicked"
        guest.left_at = datetime.now()
        meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        db.query(Meeting).filter(Meeting.id == meeting.id).update(
            {"participant_count": Meeting.participant_count - 1},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/v1/meetings/{meeting_no}/join",
        json={"password": ""},
        headers=guest_headers,
    )
    assert response.status_code == 403
    assert host_token


def test_recording_play_unknown_id_is_not_found(client, session_factory):
    _, _, headers = make_user(session_factory, "review_recording_missing")
    assert client.get("/api/v1/recordings/999999/play", headers=headers).status_code == 404
