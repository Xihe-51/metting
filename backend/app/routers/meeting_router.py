"""
会议相关路由
REST API + WebSocket
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request, Query
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from jose import JWTError, jwt
import json
import random
import os

from app.database import get_db
from app.models import User, Meeting, Participant, Recording, MeetingWhitelist, AuditLog, ChatMessage
from app.websocket_manager import manager
from app.routers.auth_router import require_auth, get_password_hash, verify_password, SECRET_KEY, ALGORITHM
from app.schemas import success, error

router = APIRouter()


# ============ Pydantic 模型（请求） ============

class CreateMeetingRequest(BaseModel):
    title: Optional[str] = ""
    password: Optional[str] = ""  # 会议密码，空字符串 = 无密码
    scheduled_at: Optional[str] = ""  # 预约时间 ISO格式，空 = 即时会议
    waiting_room: Optional[bool] = False  # 是否开启等候室
    whitelist: Optional[bool] = False  # 是否开启参会白名单


class JoinMeetingRequest(BaseModel):
    password: Optional[str] = ""  # 会议密码


class AnnouncementRequest(BaseModel):
    announcement: str = ""  # 房间公告/议程文本


# ============ 工具函数 ============

def generate_meeting_no(db: Session):
    """生成6位数字会议号（查重防碰撞）"""
    for _ in range(100):
        no = str(random.randint(100000, 999999))
        if not db.query(Meeting).filter(Meeting.meeting_no == no).first():
            return no
    # 极端情况：号段几乎用尽，继续重试直到找到未使用的
    while True:
        no = str(random.randint(100000, 999999))
        if not db.query(Meeting).filter(Meeting.meeting_no == no).first():
            return no


def get_server_host():
    """获取服务器地址（可从环境变量配置）"""
    return os.getenv("SERVER_HOST", "localhost")


def audit_log(db: Session, meeting_id: int, user_id: int, action: str, details: str = None):
    """记录审计日志"""
    try:
        log = AuditLog(meeting_id=meeting_id, user_id=user_id, action=action, details=details)
        db.add(log)
        db.commit()
    except Exception:
        pass  # 审计日志不影响主流程


def is_meeting_host(meeting: Meeting, user_id: int) -> bool:
    """判断用户是否具备主持人权限（含联席主持）；host_id 为空时默认创建者"""
    if meeting.host_id:
        return user_id in (meeting.host_id, meeting.co_host_id)
    return user_id in (meeting.creator_id, meeting.co_host_id)


# ============ REST API ============

@router.post("/meetings")
async def create_meeting(
    request: CreateMeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """创建会议"""
    meeting_no = generate_meeting_no(db)

    status = "ongoing"
    scheduled_at_dt = None
    if request.scheduled_at:
        # 预约会议，此时状态是 scheduled，未开始
        status = "scheduled"
        scheduled_at_dt = datetime.fromisoformat(request.scheduled_at)

    password_hash = None
    if request.password and request.password.strip():
        password_hash = get_password_hash(request.password)

    meeting = Meeting(
        meeting_no=meeting_no,
        title=request.title,
        creator_id=current_user.id,
        status=status,
        password_hash=password_hash,
        scheduled_at=scheduled_at_dt,
        waiting_room_enabled=request.waiting_room,
        whitelist_enabled=request.whitelist,
        participant_count=1,
        host_id=current_user.id
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
    audit_log(db, meeting.id, current_user.id, "create", json.dumps({"meeting_no": meeting_no}))
    db.commit()

    host = get_server_host()
    return success({
        "meeting_no": meeting_no,
        "title": request.title,
        "creator_name": current_user.display_name,
        "status": status,
        "scheduled_at": request.scheduled_at,
        "has_password": (password_hash is not None),
        "participant_count": 1,
        "created_at": str(meeting.created_at),
        "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={participant.id}"
    })


@router.post("/meetings/{meeting_no}/join")
async def join_meeting(
    meeting_no: str,
    request: JoinMeetingRequest = JoinMeetingRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """加入会议"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.status == "ended" and meeting.ended_at is not None:
        return JSONResponse(status_code=409, content=error(409, "会议已结束"))

    # 预约会议到点自动开始
    if meeting.status == "scheduled" and meeting.scheduled_at and meeting.scheduled_at <= datetime.now():
        meeting.status = "ongoing"
        meeting.ended_at = None
        db.commit()

    # 先检查是否已在会议中（活跃连接，无需再次验证密码和白名单）
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
            "status": existing.status,
            "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={existing.id}"
        })

    # 检查是否断线重连（之前离开但未被踢/拒，刷新页面或网络恢复）
    reconnecting = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == current_user.id,
        Participant.left_at != None,
        Participant.status.in_(["joined", "left", "waiting"])
    ).first()
    if reconnecting:
        reconnecting.left_at = None
        reconnecting.status = "joined" if reconnecting.status != "waiting" else "waiting"
        # 断线重连：恢复在线人数，并把因“全部离会”自动结束的会议重新置为进行中
        meeting.participant_count = (meeting.participant_count or 0) + 1
        if meeting.status == "ended":
            meeting.status = "ongoing"
        db.commit()
        host = get_server_host()
        return success({
            "meeting_no": meeting_no,
            "title": meeting.title,
            "status": reconnecting.status,
            "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={reconnecting.id}"
        })

    # 锁定会议：新成员无法加入（主持人/创建者/联席主持除外）
    is_privileged = current_user.id in [meeting.creator_id, meeting.host_id, meeting.co_host_id]
    if meeting.locked and not is_privileged:
        return JSONResponse(status_code=403, content=error(403, "会议已锁定，无法加入"))

    # 验证会议密码（仅新加入者需要）
    if meeting.password_hash:
        if not request.password:
            return JSONResponse(status_code=403, content=error(403, "需要会议密码"))
        if not verify_password(request.password, meeting.password_hash):
            return JSONResponse(status_code=403, content=error(403, "会议密码错误"))

    # 白名单检查
    if meeting.whitelist_enabled:
        in_whitelist = db.query(MeetingWhitelist).filter(
            MeetingWhitelist.meeting_id == meeting.id,
            MeetingWhitelist.user_id == current_user.id
        ).first()
        if not in_whitelist:
            return JSONResponse(status_code=403, content=error(403, "你不在会议白名单中"))

    # 等候室模式：新参与者状态为 waiting
    participant_status = "waiting" if meeting.waiting_room_enabled else "joined"

    participant = Participant(
        meeting_id=meeting.id,
        user_id=current_user.id,
        display_name=current_user.display_name,
        status=participant_status,
        audio_on=True,
        video_on=True
    )
    db.add(participant)
    meeting.participant_count += 1
    db.commit()
    db.refresh(participant)

    audit_log(db, meeting.id, current_user.id, "join", json.dumps({"status": participant_status}))

    host = get_server_host()
    return success({
        "meeting_no": meeting_no,
        "title": meeting.title,
        "status": participant_status,  # joined / waiting
        "websocket_url": f"ws://{host}:8000/api/v1/ws/{meeting_no}?participant_id={participant.id}"
    })


@router.post("/meetings/{meeting_no}/start")
async def start_meeting(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """开始预约会议（主持人/创建者）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.status != "scheduled":
        return JSONResponse(status_code=409, content=error(409, "会议已开始或已结束"))
    if not is_meeting_host(meeting, current_user.id):
        return JSONResponse(status_code=403, content=error(403, "仅主持人可开始会议"))

    meeting.status = "ongoing"
    meeting.ended_at = None
    db.commit()
    audit_log(db, meeting.id, current_user.id, "start")
    return success(None, "会议已开始")


@router.get("/meetings/my")
async def my_meetings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取我的会议（我创建的 + 我参加过的）"""
    # 我创建的会议
    created = db.query(Meeting).filter(
        Meeting.creator_id == current_user.id
    ).order_by(Meeting.created_at.desc()).all()

    # 我参加过的会议（通过 Participant 表）
    my_participant_records = db.query(Participant).filter(
        Participant.user_id == current_user.id
    ).all()
    participated_meeting_ids = list(set(p.meeting_id for p in my_participant_records))
    participated = []
    if participated_meeting_ids:
        participated = db.query(Meeting).filter(
            Meeting.id.in_(participated_meeting_ids),
            Meeting.creator_id != current_user.id  # 排除自己创建的
        ).order_by(Meeting.created_at.desc()).all()

    created_set = set(m.id for m in created)

    def fmt_meeting(m, role: str):
        return {
            "meeting_no": m.meeting_no,
            "title": m.title,
            "status": m.status,
            "role": role,
            "creator_name": m.creator.display_name,
            "participant_count": m.participant_count,
            "has_password": m.password_hash is not None,
            "created_at": str(m.created_at) if m.created_at else None,
            "ended_at": str(m.ended_at) if m.ended_at else None,
            "scheduled_at": str(m.scheduled_at) if m.scheduled_at else None
        }

    created_list = [fmt_meeting(m, "creator") for m in created]
    participated_list = [fmt_meeting(m, "participant") for m in participated if m.id not in created_set]

    return success({
        "created": created_list,
        "participated": participated_list
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
        "has_password": meeting.password_hash is not None,
        "participant_count": meeting.participant_count,
        "announcement": meeting.announcement or "",
        "locked": meeting.locked,
        "is_host": is_meeting_host(meeting, current_user.id),
        "is_creator": meeting.creator_id == current_user.id,
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


@router.post("/meetings/{meeting_no}/announcement")
async def set_announcement(
    meeting_no: str,
    request: AnnouncementRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """设置房间公告/议程（主持人）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if not is_meeting_host(meeting, current_user.id):
        return JSONResponse(status_code=403, content=error(403, "仅主持人可设置公告"))

    meeting.announcement = request.announcement
    db.commit()

    audit_log(db, meeting.id, current_user.id, "set_announcement", request.announcement)

    await manager.broadcast_to_meeting(meeting_no, {
        "type": "announcement",
        "payload": {"announcement": request.announcement, "by": current_user.display_name}
    })

    return success(None, "公告已更新")


@router.get("/meetings/{meeting_no}/messages")
async def get_messages(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取聊天历史（群聊 + 与当前用户相关的私聊）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))

    # 当前用户在会议中的 participant 记录（可能有多个历史记录）
    my_pids = [p.id for p in db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == current_user.id
    ).all()]

    messages = db.query(ChatMessage).filter(
        ChatMessage.meeting_id == meeting.id,
        or_(
            ChatMessage.is_whisper == False,  # 所有群聊
            ChatMessage.participant_id.in_(my_pids) if my_pids else False,  # 我发的私聊
            ChatMessage.target_id.in_(my_pids) if my_pids else False  # 发给我的私聊
        )
    ).order_by(ChatMessage.created_at.asc()).all()

    return success([
        {
            "id": m.id,
            "from_id": m.participant_id,
            "from_name": m.display_name,
            "content": m.content,
            "is_whisper": m.is_whisper,
            "target_id": m.target_id,
            "recalled": bool(m.recalled),
            "is_self": m.participant_id in my_pids,
            "created_at": str(m.created_at) if m.created_at else None
        }
        for m in messages
    ])


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

    audit_log(db, meeting.id, current_user.id, "end")

    await manager.broadcast_to_meeting(meeting_no, {
        "type": "meeting_ended",
        "payload": {"by": current_user.display_name}
    })

    return success(None, "会议已结束")


@router.get("/meetings/{meeting_no}/report")
async def meeting_report(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """会议结束统计报告：参会时长、发言次数、进出时间等出勤数据（会议相关人员可查看）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))

    # 仅创建者、主持/联席主持、或曾参会的成员可查看
    is_creator = meeting.creator_id == current_user.id
    is_host = is_meeting_host(meeting, current_user.id)
    is_attended = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == current_user.id
    ).first() is not None
    if not (is_creator or is_host or is_attended):
        return JSONResponse(status_code=403, content=error(403, "无权查看该会议报告"))

    participants = db.query(Participant).filter(
        Participant.meeting_id == meeting.id
    ).order_by(Participant.joined_at.asc()).all()

    meeting_end = meeting.ended_at or datetime.now()
    report_rows = []
    for p in participants:
        joined = p.joined_at
        left = p.left_at if p.left_at else meeting_end
        # 参会时长（秒）
        seconds = 0
        if joined and left and left > joined:
            seconds = int((left - joined).total_seconds())
        minutes = round(seconds / 60, 1)
        # 发言次数：群聊消息数（不含私聊、不含已撤回）
        speech_count = db.query(ChatMessage).filter(
            ChatMessage.meeting_id == meeting.id,
            ChatMessage.participant_id == p.id,
            ChatMessage.is_whisper == False,
            ChatMessage.recalled == False
        ).count()
        report_rows.append({
            "participant_id": p.id,
            "user_id": p.user_id,
            "display_name": p.display_name,
            "role": "creator" if p.user_id == meeting.creator_id else ("host" if p.user_id in (meeting.host_id, meeting.co_host_id) else "member"),
            "status": p.status,
            "joined_at": str(p.joined_at) if p.joined_at else None,
            "left_at": str(p.left_at) if p.left_at else None,
            "duration_minutes": minutes,
            "speech_count": speech_count
        })

    return success({
        "meeting_no": meeting.meeting_no,
        "title": meeting.title,
        "creator_name": meeting.creator.display_name,
        "created_at": str(meeting.created_at) if meeting.created_at else None,
        "ended_at": str(meeting.ended_at) if meeting.ended_at else None,
        "status": meeting.status,
        "participants": report_rows
    })


@router.get("/meetings")
async def list_meetings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取进行中的会议列表"""
    ongoing = db.query(Meeting).filter(Meeting.status == "ongoing").all()
    scheduled = db.query(Meeting).filter(Meeting.status == "scheduled").all()

    return success({
        "ongoing": [
            {
                "meeting_no": m.meeting_no,
                "title": m.title,
                "participant_count": m.participant_count,
                "has_password": m.password_hash is not None
            }
            for m in ongoing
        ],
        "scheduled": [
            {
                "meeting_no": m.meeting_no,
                "title": m.title,
                "scheduled_at": str(m.scheduled_at) if m.scheduled_at else "",
                "has_password": m.password_hash is not None
            }
            for m in scheduled
        ]
    })


@router.get("/recordings/my")
async def my_recordings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取我的录制（我发起的录制列表）"""
    # 我创建的会议
    my_meeting_ids = db.query(Meeting.id).filter(
        Meeting.creator_id == current_user.id
    ).all()
    my_meeting_ids = [m[0] for m in my_meeting_ids]

    if not my_meeting_ids:
        return success([])

    recordings = db.query(Recording).filter(
        Recording.meeting_id.in_(my_meeting_ids)
    ).order_by(Recording.started_at.desc()).all()

    # 获取会议信息
    meeting_map = {}
    meetings = db.query(Meeting).filter(Meeting.id.in_(my_meeting_ids)).all()
    for m in meetings:
        meeting_map[m.id] = m

    return success([
        {
            "id": r.id,
            "file_name": r.file_name,
            "file_size": r.file_size,
            "duration": r.duration,
            "status": r.status,
            "meeting_no": meeting_map[r.meeting_id].meeting_no if r.meeting_id in meeting_map else "",
            "meeting_title": meeting_map[r.meeting_id].title if r.meeting_id in meeting_map else "",
            "started_at": str(r.started_at),
            "ended_at": str(r.ended_at) if r.ended_at else None
        }
        for r in recordings
    ])


# ============ 审计日志接口 ============

@router.get("/meetings/{meeting_no}/audit_logs")
async def get_audit_logs(
    meeting_no: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取会议审计日志（仅创建者可查看）"""
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.creator_id != current_user.id:
        return JSONResponse(status_code=403, content=error(403, "仅创建者可查看审计日志"))

    logs = db.query(AuditLog).filter(
        AuditLog.meeting_id == meeting.id
    ).order_by(AuditLog.created_at.asc()).all()

    # 获取所有涉及的用户名
    user_ids = list(set(log.user_id for log in logs))
    users = {u.id: u.display_name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    return success([
        {
            "id": log.id,
            "action": log.action,
            "user_name": users.get(log.user_id, f"用户{log.user_id}"),
            "details": log.details,
            "created_at": str(log.created_at)
        }
        for log in logs
    ])


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

    # 确保录制目录存在
    os.makedirs("recordings", exist_ok=True)

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

    audit_log(db, meeting.id, current_user.id, "record_start")

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

    # 校验录制归属，防止越权停止他人会议录像
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if recording.meeting_id != meeting.id:
        return JSONResponse(status_code=403, content=error(403, "录制不属于该会议"))
    if meeting.creator_id != current_user.id:
        return JSONResponse(status_code=403, content=error(403, "仅会议创建者可停止录制"))

    recording.status = "completed"
    recording.ended_at = datetime.now()
    if recording.started_at:
        recording.duration = int((recording.ended_at - recording.started_at).total_seconds())
    db.commit()
    db.refresh(recording)

    audit_log(db, recording.meeting_id, current_user.id, "record_stop")

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


@router.post("/meetings/{meeting_no}/recordings/{recording_id}/chunk")
async def upload_recording_chunk(
    meeting_no: str,
    recording_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """上传录制分片（原始二进制数据）"""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        return JSONResponse(status_code=404, content=error(404, "录制不存在"))
    if recording.status != "recording":
        return JSONResponse(status_code=400, content=error(400, "录制已结束"))

    # 校验录制归属，防止越权向他人会议录像写入分片
    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if recording.meeting_id != meeting.id:
        return JSONResponse(status_code=403, content=error(403, "录制不属于该会议"))
    if meeting.creator_id != current_user.id:
        return JSONResponse(status_code=403, content=error(403, "仅会议创建者可上传录制"))

    # 读取原始二进制数据
    chunk = await request.body()
    if not chunk:
        return JSONResponse(status_code=400, content=error(400, "空数据"))

    # 追加写入文件
    file_path = os.path.join("recordings", recording.file_name)
    with open(file_path, "ab") as f:
        f.write(chunk)

    # 更新文件大小
    recording.file_size = os.path.getsize(file_path)
    db.commit()

    return JSONResponse(content=success({"file_size": recording.file_size}))


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
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """回放录制（返回视频文件流）"""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        return JSONResponse(status_code=404, content=error(404, "录制记录不存在"))

    # 仅该会议的创建者或参会者可回放
    meeting = db.query(Meeting).filter(Meeting.id == recording.meeting_id).first()
    is_member = meeting is not None and (
        meeting.creator_id == current_user.id
        or db.query(Participant).filter(
            Participant.meeting_id == meeting.id,
            Participant.user_id == current_user.id
        ).first() is not None
    )
    if not is_member:
        return JSONResponse(status_code=403, content=error(403, "无权访问该录制"))

    file_path = recording.file_path
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content=error(404, "录制文件不存在"))

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="video/webm",
        headers={"Content-Disposition": "inline"}
    )


@router.get("/recordings/{recording_id}/download")
async def download_recording(
    recording_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """下载录制文件"""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if not recording:
        return JSONResponse(status_code=404, content=error(404, "录制记录不存在"))

    # 仅该会议的创建者或参会者可下载
    meeting = db.query(Meeting).filter(Meeting.id == recording.meeting_id).first()
    is_member = meeting is not None and (
        meeting.creator_id == current_user.id
        or db.query(Participant).filter(
            Participant.meeting_id == meeting.id,
            Participant.user_id == current_user.id
        ).first() is not None
    )
    if not is_member:
        return JSONResponse(status_code=403, content=error(403, "无权访问该录制"))

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
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    """WebSocket连接（携带 JWT 鉴权，防止伪造 participant_id）"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
    except JWTError:
        user_id = None
    if user_id is None:
        await websocket.close(code=1008)
        return

    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        await websocket.close()
        return

    participant = db.query(Participant).filter(Participant.id == participant_id).first()
    if not participant or participant.user_id != user_id:
        await websocket.close(code=1008)
        return

    # 主持人（含联席主持）
    is_host = is_meeting_host(meeting, participant.user_id)
    is_waiting = (participant.status == "waiting")

    # 获取用户头像
    user = db.query(User).filter(User.id == participant.user_id).first()
    avatar_url = user.avatar_url if user and user.avatar_url else ""

    await manager.connect(
        websocket,
        meeting_no,
        participant_id,
        {
            "name": participant.display_name,
            "audio_on": participant.audio_on,
            "video_on": participant.video_on,
            "sharing_screen": False,
            "is_host": is_host,
            "muted": participant.muted,
            "chat_muted": participant.chat_muted,
            "status": participant.status,
            "avatar_url": avatar_url
        }
    )

    # 等待中的参与者只看到自己，看不到其他人
    if is_waiting:
        await manager.send_personal_message({
            "type": "waiting_room",
            "payload": {"message": "请稍候，主持人即将邀请您加入会议"}
        }, meeting_no, participant_id)
    else:
        await manager.send_personal_message({
            "type": "participants_list",
            "payload": manager.get_participants(meeting_no)
        }, meeting_no, participant_id)
    
    # 主持人收到等候室列表
    if is_host:
        await manager.send_personal_message({
            "type": "waiting_participants",
            "payload": manager.get_waiting_participants(meeting_no)
        }, meeting_no, participant_id)

    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            payload = data.get("payload", {})

            if message_type == "chat":
                content = (payload.get("content") or "").strip()
                if participant.chat_muted:
                    await manager.send_personal_message({
                        "type": "chat_muted",
                        "payload": {"message": "你已被主持人禁言，无法发送消息"}
                    }, meeting_no, participant_id)
                    continue
                if not content:
                    continue
                now = datetime.now()
                msg = ChatMessage(
                    meeting_id=meeting.id,
                    participant_id=participant_id,
                    display_name=participant.display_name,
                    content=content,
                    is_whisper=False
                )
                db.add(msg)
                db.commit()
                db.refresh(msg)
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "chat",
                    "payload": {
                        "id": msg.id,
                        "from_id": participant_id,
                        "from_name": participant.display_name,
                        "content": content,
                        "time": str(now)
                    }
                })

            elif message_type == "whisper":
                # 私聊（群成员间）
                target_id = payload.get("target_id")
                content = (payload.get("content") or "").strip()
                if not target_id or not content:
                    continue
                if participant.chat_muted:
                    await manager.send_personal_message({
                        "type": "chat_muted",
                        "payload": {"message": "你已被主持人禁言，无法发送消息"}
                    }, meeting_no, participant_id)
                    continue
                now = datetime.now()
                target_name = manager.get_participant_name(meeting_no, target_id)
                db.add(ChatMessage(
                    meeting_id=meeting.id,
                    participant_id=participant_id,
                    display_name=participant.display_name,
                    content=content,
                    is_whisper=True,
                    target_id=target_id
                ))
                db.commit()
                await manager.send_personal_message({
                    "type": "whisper",
                    "payload": {
                        "from_id": participant_id,
                        "from_name": participant.display_name,
                        "content": content,
                        "time": str(now)
                    }
                }, meeting_no, target_id)
                # 也发一份回给自己
                await manager.send_personal_message({
                    "type": "whisper",
                    "payload": {
                        "from_id": participant_id,
                        "from_name": participant.display_name,
                        "content": content,
                        "to_name": target_name,
                        "time": str(now)
                    }
                }, meeting_no, participant_id)

            elif message_type == "recall":
                # 撤回群聊消息（仅发送者本人，且是群聊并非私聊）
                message_id = payload.get("message_id")
                if not message_id:
                    continue
                target_msg = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
                if not target_msg or target_msg.meeting_id != meeting.id:
                    continue
                if target_msg.participant_id != participant_id:
                    continue  # 只能撤回自己的消息
                if target_msg.is_whisper:
                    continue  # 私聊暂不支持撤回
                if target_msg.recalled:
                    continue
                target_msg.recalled = True
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "recall_message", json.dumps({"message_id": message_id}))
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "message_recalled",
                    "payload": {
                        "id": message_id,
                        "from_id": participant_id,
                        "from_name": participant.display_name
                    }
                })

            elif message_type == "reaction":
                # 表情反应，广播给所有人
                emoji = payload.get("emoji", "")
                if emoji:
                    await manager.broadcast_to_meeting(meeting_no, {
                        "type": "reaction",
                        "payload": {
                            "from_id": participant_id,
                            "from_name": participant.display_name,
                            "emoji": emoji
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

            elif message_type == "lock_meeting":
                # 主持人锁定会议（禁止新成员加入）
                if not manager.is_host(meeting_no, participant_id):
                    continue
                meeting.locked = True
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "lock")
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "meeting_locked",
                    "payload": {"locked": True, "by": participant.display_name}
                })

            elif message_type == "unlock_meeting":
                # 主持人解锁会议
                if not manager.is_host(meeting_no, participant_id):
                    continue
                meeting.locked = False
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "unlock")
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "meeting_locked",
                    "payload": {"locked": False, "by": participant.display_name}
                })

            elif message_type == "transfer_host":
                # 主持人转移主持身份
                if not manager.is_host(meeting_no, participant_id):
                    continue
                target_id = payload.get("target_id")
                if not target_id or target_id == participant_id:
                    continue
                target = db.query(Participant).filter(Participant.id == target_id).first()
                if not target:
                    continue
                meeting.host_id = target.user_id
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "transfer_host", json.dumps({"target_name": target.display_name}))
                manager.update_participant_status(meeting_no, participant_id, {"is_host": False})
                manager.update_participant_status(meeting_no, target_id, {"is_host": True})
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "participants_list",
                    "payload": manager.get_participants(meeting_no)
                })
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "host_changed",
                    "payload": {"name": target.display_name, "by": participant.display_name}
                })

            elif message_type == "set_co_host":
                # 主持人设置/取消联席主持
                if not manager.is_host(meeting_no, participant_id):
                    continue
                target_id = payload.get("target_id")
                enabled = bool(payload.get("enabled", True))
                if not target_id:
                    continue
                target = db.query(Participant).filter(Participant.id == target_id).first()
                if not target:
                    continue
                meeting.co_host_id = target.user_id if enabled else None
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "set_co_host", json.dumps({"target_name": target.display_name, "enabled": enabled}))
                manager.update_participant_status(meeting_no, target_id, {"is_host": enabled})
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "participants_list",
                    "payload": manager.get_participants(meeting_no)
                })
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "co_host_changed",
                    "payload": {"name": target.display_name, "enabled": enabled, "by": participant.display_name}
                })

            elif message_type == "mute_user":
                # 主持人禁言某个成员（禁麦/禁聊）
                if not manager.is_host(meeting_no, participant_id):
                    continue
                target_id = payload.get("target_id")
                if not target_id or target_id == participant_id:
                    continue
                target = db.query(Participant).filter(Participant.id == target_id).first()
                if not target:
                    continue
                mute_audio = payload.get("mute_audio")  # True/False/None
                mute_chat = payload.get("mute_chat")
                if mute_audio is not None:
                    target.muted = bool(mute_audio)
                    if mute_audio:
                        target.audio_on = False
                if mute_chat is not None:
                    target.chat_muted = bool(mute_chat)
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "mute_user", json.dumps({"target_name": target.display_name, "mute_audio": bool(target.muted), "mute_chat": bool(target.chat_muted)}))
                manager.update_participant_status(meeting_no, target_id, {
                    "muted": bool(target.muted),
                    "chat_muted": bool(target.chat_muted),
                    "audio_on": bool(target.audio_on)
                })
                await manager.send_personal_message({
                    "type": "mute_status",
                    "payload": {"mute_audio": bool(target.muted), "mute_chat": bool(target.chat_muted), "by": participant.display_name}
                }, meeting_no, target_id)
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "participants_list",
                    "payload": manager.get_participants(meeting_no)
                })

            elif message_type == "speaking":
                # 语音激励：本地检测到说话后上报，广播给其他人
                is_speaking = bool(payload.get("speaking", False))
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "speaking",
                    "payload": {
                        "user_id": participant_id,
                        "speaking": is_speaking
                    }
                }, exclude_id=participant_id)

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
                audit_log(db, meeting.id, participant.user_id, "mute_all")
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
                    target_participant.status = "kicked"
                    meeting.participant_count = max(0, meeting.participant_count - 1)
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "kick", json.dumps({"target_user_id": target_participant.user_id, "target_name": target_participant.display_name}))
                await manager.kick_participant(meeting_no, target_id, participant.display_name)

            elif message_type == "admit_user":
                # 主持人准入等候室中的参会者
                if not manager.is_host(meeting_no, participant_id):
                    continue
                target_id = payload.get("target_id")
                if not target_id:
                    continue
                # 更新数据库状态
                target_participant = db.query(Participant).filter(Participant.id == target_id).first()
                if target_participant and target_participant.status == "waiting":
                    target_participant.status = "joined"
                    db.commit()
                    audit_log(db, meeting.id, participant.user_id, "admit", json.dumps({"target_user_id": target_participant.user_id, "target_name": target_participant.display_name}))
                await manager.admit_participant(meeting_no, target_id)

            elif message_type == "reject_user":
                # 主持人拒绝等候室中的参会者
                if not manager.is_host(meeting_no, participant_id):
                    continue
                target_id = payload.get("target_id")
                if not target_id:
                    continue
                target_participant = db.query(Participant).filter(Participant.id == target_id).first()
                if target_participant and target_participant.status == "waiting":
                    target_participant.status = "rejected"
                    target_participant.left_at = datetime.now()
                    meeting.participant_count = max(0, meeting.participant_count - 1)
                    db.commit()
                    audit_log(db, meeting.id, participant.user_id, "reject", json.dumps({"target_user_id": target_participant.user_id, "target_name": target_participant.display_name}))
                await manager.reject_participant(meeting_no, target_id)

            elif message_type == "leave_meeting":
                # 用户主动退出会议
                participant.left_at = datetime.now()
                participant.status = "left"
                meeting.participant_count = max(0, meeting.participant_count - 1)
                if meeting.participant_count == 0:
                    meeting.status = "ended"
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "leave")
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "user_left",
                    "payload": {"id": participant_id, "name": participant.display_name}
                })
                await manager.disconnect(meeting_no, participant_id)
                break  # 退出循环，正常关闭连接

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
        # 掉线/关闭页面：更新离会信息，保证 participant_count 准确
        fresh_participant = db.query(Participant).filter(Participant.id == participant_id).first()
        fresh_meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
        if fresh_participant and fresh_participant.left_at is None:
            fresh_participant.left_at = datetime.now()
            fresh_participant.status = "left"
            if fresh_meeting:
                fresh_meeting.participant_count = max(0, (fresh_meeting.participant_count or 0) - 1)
                if fresh_meeting.participant_count == 0:
                    fresh_meeting.status = "ended"
            db.commit()
            audit_log(db, fresh_meeting.id, fresh_participant.user_id, "disconnect")