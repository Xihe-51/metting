"""
会议相关路由
REST API + WebSocket
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import random
import os

from app.database import get_db
from app.models import Meeting, Participant, Recording
from app.websocket_manager import manager

router = APIRouter()


# ============ Pydantic 模型（请求/响应） ============

class CreateMeetingRequest(BaseModel):
    creator_name: str
    title: Optional[str] = ""


class JoinMeetingRequest(BaseModel):
    display_name: str


class MeetingResponse(BaseModel):
    meeting_no: str
    title: str
    creator_name: str
    status: str
    participant_count: int
    websocket_url: str


# ============ 工具函数 ============

def generate_meeting_no():
    """生成6位数字会议号"""
    while True:
        no = str(random.randint(100000, 999999))
        if no not in ["123456", "111111", "000000", "666666", "888888"]:
            return no


# ============ REST API ============

@router.post("/meetings", response_model=MeetingResponse)
async def create_meeting(request: CreateMeetingRequest, db: Session = Depends(get_db)):
    """创建会议"""
    meeting_no = generate_meeting_no()

    meeting = Meeting(
        meeting_no=meeting_no,
        title=request.title,
        creator_name=request.creator_name,
        status="ongoing",
        participant_count=1
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    participant = Participant(
        meeting_id=meeting.id,
        display_name=request.creator_name,
        audio_on=True,
        video_on=True
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)

    return MeetingResponse(
        meeting_no=meeting_no,
        title=request.title,
        creator_name=request.creator_name,
        status="ongoing",
        participant_count=1,
        websocket_url=f"ws://localhost:8000/ws/{meeting_no}?participant_id={participant.id}"
    )


@router.post("/meetings/{meeting_no}/join", response_model=MeetingResponse)
async def join_meeting(meeting_no: str, request: JoinMeetingRequest, db: Session = Depends(get_db)):
    """加入会议"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="会议不存在")

    if meeting.status == "ended":
        raise HTTPException(status_code=409, detail="会议已结束")

    participant = Participant(
        meeting_id=meeting.id,
        display_name=request.display_name,
        audio_on=True,
        video_on=True
    )
    db.add(participant)
    meeting.participant_count += 1
    db.commit()
    db.refresh(participant)

    return MeetingResponse(
        meeting_no=meeting_no,
        title=meeting.title,
        creator_name=meeting.creator_name,
        status=meeting.status,
        participant_count=meeting.participant_count,
        websocket_url=f"ws://localhost:8000/ws/{meeting_no}?participant_id={participant.id}"
    )


@router.get("/meetings/{meeting_no}")
async def get_meeting(meeting_no: str, db: Session = Depends(get_db)):
    """获取会议信息"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="会议不存在")

    participants = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.left_at == None
    ).all()

    return {
        "meeting_no": meeting.meeting_no,
        "title": meeting.title,
        "creator_name": meeting.creator_name,
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
    }


@router.post("/meetings/{meeting_no}/end")
async def end_meeting(meeting_no: str, db: Session = Depends(get_db)):
    """结束会议"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        raise HTTPException(status_code=404, detail="会议不存在")

    meeting.status = "ended"
    meeting.ended_at = datetime.now()
    db.commit()

    await manager.broadcast_to_meeting(meeting_no, {
        "type": "meeting_ended",
        "payload": {"by": meeting.creator_name}
    })

    return {"message": "会议已结束"}


@router.get("/meetings")
async def list_meetings(db: Session = Depends(get_db)):
    """获取会议列表"""
    meetings = db.query(Meeting).filter(Meeting.status == "ongoing").all()

    return {
        "ongoing": [
            {
                "meeting_no": m.meeting_no,
                "title": m.title,
                "participant_count": m.participant_count
            }
            for m in meetings
        ]
    }


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

    await manager.connect(
        websocket,
        meeting_no,
        participant_id,
        {
            "name": participant.display_name,
            "audio_on": participant.audio_on,
            "video_on": participant.video_on,
            "sharing_screen": False
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
        meeting.participant_count -= 1
        db.commit()