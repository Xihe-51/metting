"""
会议相关路由
REST API + WebSocket
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import random
import os

from app.database import get_db
from app.models import Meeting, Participant, Recording, User
from app.websocket_manager import manager
from app.routers.auth_router import require_auth
from app.schemas import success, error

router = APIRouter()


# ============ Pydantic 模型（请求） ============

class CreateMeetingRequest(BaseModel):
    title: Optional[str] = ""


# ============ 工具函数 ============

def generate_meeting_no():
    """生成6位数字会议号"""
    while True:
        no = str(random.randint(100000, 999999))
        return no


def get_server_host():
    """获取服务器地址（可从环境变量配置）"""
    return os.getenv("SERVER_HOST", "localhost")


# ============ REST API ============

@router.post("/meetings")
async def create_meeting(
    request: CreateMeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """创建会议"""
    meeting_no = generate_meeting_no()

    meeting = Meeting(
        meeting_no=meeting_no,
        title=request.title,
        creator_id=current_user.id,
        status="ongoing",
        participant_count=1
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    participant = Participant(
        meeting_id=meeting.id,
        user_id=current_user.id,
        display_name=current_user.display_name,
        audio_on=True,
        video_on=True
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)

    host = get_server_host()
    return success({
        "meeting_no": meeting_no,
        "title": request.title,
        "creator_name": current_user.display_name,
        "status": "ongoing",
        "participant_count": 1,
        "created_at": str(meeting.created_at),
        "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={participant.id}"
    })


@router.post("/meetings/{meeting_no}/join")
async def join_meeting(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """加入会议"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.status == "ended":
        return JSONResponse(status_code=409, content=error(409, "会议已结束"))

    # 检查是否已在会议中
    existing = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == current_user.id,
        Participant.left_at == None
    ).first()
    if existing:
        host = get_server_host()
        return success({
            "meeting_no": meeting_no,
            "title": meeting.title,
            "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={existing.id}"
        })

    participant = Participant(
        meeting_id=meeting.id,
        user_id=current_user.id,
        display_name=current_user.display_name,
        audio_on=True,
        video_on=True
    )
    db.add(participant)
    meeting.participant_count += 1
    db.commit()
    db.refresh(participant)

    host = get_server_host()
    return success({
        "meeting_no": meeting_no,
        "title": meeting.title,
        "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={participant.id}"
    })


@router.get("/meetings/{meeting_no}")
async def get_meeting(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取会议信息"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))

    creator = db.query(User).filter(User.id == meeting.creator_id).first()

    participants = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.left_at == None
    ).all()

    return success({
        "meeting_no": meeting.meeting_no,
        "title": meeting.title,
        "creator_name": creator.display_name if creator else "",
        "status": meeting.status,
        "participant_count": meeting.participant_count,
        "participants": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "audio_on": p.audio_on,
                "video_on": p.video_on,
                "sharing_screen": p.sharing_screen
            }
            for p in participants
        ]
    })


@router.post("/meetings/{meeting_no}/end")
async def end_meeting(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """结束会议（仅创建者可操作）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.creator_id != current_user.id:
        return JSONResponse(status_code=403, content=error(403, "仅会议创建者可结束会议"))

    meeting.status = "ended"
    meeting.ended_at = datetime.now()
    db.commit()

    await manager.broadcast_to_meeting(meeting_no, {
        "type": "meeting_ended",
        "payload": {"by": current_user.display_name}
    })

    return success(None, "会议已结束")


@router.get("/meetings")
async def list_meetings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取进行中的会议列表"""
    meetings = db.query(Meeting).filter(Meeting.status == "ongoing").all()

    return success({
        "ongoing": [
            {
                "meeting_no": m.meeting_no,
                "title": m.title,
                "participant_count": m.participant_count
            }
            for m in meetings
        ]
    })


# ============ 录制接口 ============

@router.post("/meetings/{meeting_no}/recordings/start")
async def start_recording(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """开始录制（仅创建者可操作）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.creator_id != current_user.id:
        return JSONResponse(status_code=403, content=error(403, "仅会议创建者可开始录制"))

    # 检查是否已有进行中的录制
    existing = db.query(Recording).filter(
        Recording.meeting_id == meeting.id,
        Recording.status == "recording"
    ).first()
    if existing:
        return JSONResponse(status_code=400, content=error(400, "已有进行中的录制"))

    now = datetime.now()
    file_name = f"recording_{meeting_no}_{now.strftime('%Y%m%d_%H%M%S')}.webm"
    file_path = os.path.join("recordings", file_name)

    recording = Recording(
        meeting_id=meeting.id,
        file_name=file_name,
        file_path=file_path,
        status="recording",
        started_at=now
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)

    # 广播录制开始
    await manager.broadcast_to_meeting(meeting_no, {
        "type": "recording_started",
        "payload": {"recording_id": recording.id, "by": current_user.display_name}
    })

    return success({
        "recording_id": recording.id,
        "file_name": file_name,
        "status": "recording"
    })


@router.post("/meetings/{meeting_no}/recordings/{recording_id}/stop")
async def stop_recording(
    meeting_no: str,
    recording_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """停止录制"""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        return JSONResponse(status_code=404, content=error(404, "录制记录不存在"))
    if recording.status != "recording":
        return JSONResponse(status_code=400, content=error(400, "录制已结束"))

    recording.status = "completed"
    recording.ended_at = datetime.now()
    db.commit()
    db.refresh(recording)

    # 广播录制停止
    await manager.broadcast_to_meeting(meeting_no, {
        "type": "recording_stopped",
        "payload": {"recording_id": recording_id}
    })

    return success({
        "recording_id": recording.id,
        "file_size": recording.file_size,
        "duration": recording.duration,
        "status": "completed"
    })


@router.get("/meetings/{meeting_no}/recordings")
async def list_recordings(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取录制列表"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))

    recordings = db.query(Recording).filter(
        Recording.meeting_id == meeting.id
    ).order_by(Recording.started_at.desc()).all()

    return success([
        {
            "id": r.id,
            "file_name": r.file_name,
            "file_size": r.file_size,
            "duration": r.duration,
            "status": r.status
        }
        for r in recordings
    ])


@router.get("/recordings/{recording_id}/play")
async def play_recording(
    recording_id: int,
    db: Session = Depends(get_db)
):
    """回放录制（返回视频文件流）"""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        return JSONResponse(status_code=404, content=error(404, "录制记录不存在"))

    file_path = recording.file_path
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content=error(404, "录制文件不存在"))

    return FileResponse(
        file_path,
        media_type="video/webm",
        filename=recording.file_name
    )


@router.get("/recordings/{recording_id}/download")
async def download_recording(
    recording_id: int,
    db: Session = Depends(get_db)
):
    """下载录制文件"""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        return JSONResponse(status_code=404, content=error(404, "录制记录不存在"))

    file_path = recording.file_path
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content=error(404, "录制文件不存在"))

    return FileResponse(
        file_path,
        media_type="video/webm",
        filename=recording.file_name,
        headers={"Content-Disposition": f'attachment; filename="{recording.file_name}"'}
    )


# ============ WebSocket ============

@router.websocket("/ws/{meeting_no}")
async def websocket_endpoint(
    websocket: WebSocket,
    meeting_no: str,
    participant_id: int,
    db: Session = Depends(get_db)
):
    """WebSocket连接"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        await websocket.close()
        return

    participant = db.query(Participant).filter(Participant.id == participant_id).first()
    if not participant:
        await websocket.close()
        return

    # 创建者是主持人
    is_host = (meeting.creator_id == participant.user_id)
    await manager.connect(
        websocket,
        meeting_no,
        participant_id,
        {
            "name": participant.display_name,
            "audio_on": participant.audio_on,
            "video_on": participant.video_on,
            "sharing_screen": False,
            "is_host": is_host
        }
    )

    await manager.send_personal_message({
        "type": "participants_list",
        "payload": manager.get_participants(meeting_no)
    }, meeting_no, participant_id)

    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            payload = data.get("payload", {})

            if message_type == "chat":
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "chat",
                    "payload": {
                        "from_id": participant_id,
                        "from_name": participant.display_name,
                        "content": payload.get("content", "")
                    }
                })

            elif message_type == "device":
                manager.update_participant_status(meeting_no, participant_id, {
                    "audio_on": payload.get("audio_on", True),
                    "video_on": payload.get("video_on", True)
                })
                participant.audio_on = payload.get("audio_on", True)
                participant.video_on = payload.get("video_on", True)
                db.commit()

                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "device",
                    "payload": {
                        "user_id": participant_id,
                        "audio_on": payload.get("audio_on", True),
                        "video_on": payload.get("video_on", True)
                    }
                })

            elif message_type == "screen":
                manager.update_participant_status(meeting_no, participant_id, {
                    "sharing_screen": payload.get("sharing", False)
                })
                participant.sharing_screen = payload.get("sharing", False)
                db.commit()

                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "screen",
                    "payload": {
                        "user_id": participant_id,
                        "sharing": payload.get("sharing", False)
                    }
                })

            elif message_type == "ping":
                await manager.send_personal_message({"type": "pong"}, meeting_no, participant_id)

            elif message_type == "force_mute_all":
                # 主持人全体静音
                if not manager.is_host(meeting_no, participant_id):
                    continue
                all_participants = manager.get_participants(meeting_no)
                for p in all_participants:
                    if p["id"] != participant_id:
                        await manager.send_personal_message({
                            "type": "force_mute",
                            "payload": {"by": participant.display_name}
                        }, meeting_no, p["id"])
                        manager.update_participant_status(meeting_no, p["id"], {"audio_on": False})
                        # 更新数据库
                        target = db.query(Participant).filter(Participant.id == p["id"]).first()
                        if target:
                            target.audio_on = False
                db.commit()
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "device",
                    "payload": {"user_id": 0, "audio_on": False, "video_on": True, "all_mute": True}
                })

            elif message_type == "kick_user":
                # 主持人踢人
                if not manager.is_host(meeting_no, participant_id):
                    continue
                target_id = payload.get("target_id")
                if not target_id or target_id == participant_id:
                    continue
                target_participant = db.query(Participant).filter(Participant.id == target_id).first()
                if target_participant:
                    target_participant.left_at = datetime.now()
                    meeting.participant_count = max(0, meeting.participant_count - 1)
                db.commit()
                await manager.kick_participant(meeting_no, target_id, participant.display_name)

            elif message_type in ["offer", "answer", "ice"]:
                target_id = payload.get("target_id")
                if target_id:
                    await manager.send_personal_message({
                        "type": message_type,
                        "payload": {
                            "from_id": participant_id,
                            "sdp": payload.get("sdp"),
                            "candidate": payload.get("candidate")
                        }
                    }, meeting_no, target_id)

    except WebSocketDisconnect:
        await manager.disconnect(meeting_no, participant_id)
        participant.left_at = datetime.now()
        meeting.participant_count = max(0, meeting.participant_count - 1)
        db.commit()