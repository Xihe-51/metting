"""
会议相关路由
REST API + WebSocket
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
import asyncio
import json
import secrets
import threading
import time
import os

from app import rate_limit
from app.config import load_recording_quota, load_max_participants, load_video_seat_limit
from app.database import get_db
from app.models import User, Meeting, Participant, Recording, MeetingWhitelist, AuditLog, ChatMessage
from app.websocket_manager import manager
from app.routers.auth_router import require_auth, get_password_hash, verify_password, resolve_user_from_token
from app.schemas import success, error

router = APIRouter()

# ============ 限流阈值（会议号枚举防护，见 app/rate_limit.py） ============

JOIN_LIMIT_PER_USER = 30       # /join：单账号 60s 内最多 30 次
JOIN_LIMIT_PER_IP = 90         # /join：单 IP 60s 内最多 90 次
MEETING_INFO_LIMIT_PER_USER = 60   # 会议信息查询：单账号 60s 内最多 60 次
MEETING_INFO_LIMIT_PER_IP = 180    # 会议信息查询：单 IP 60s 内最多 180 次
RATE_WINDOW = 60.0

# 会议号唯一约束冲突后的重试次数
MAX_MEETING_NO_RETRY = 5


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

def _random_meeting_no() -> str:
    """生成一个 6 位数字会议号（加密安全随机）"""
    return str(secrets.randbelow(900000) + 100000)


def generate_meeting_no(db: Session):
    """生成6位数字会议号（加密安全随机 + 查重）

    使用 secrets 而非 random：random 是可预测的梅森旋转算法，攻击者观察若干
    会议号后即可推测后续号码，从而预判/抢占他人会议。
    这里的查重只能降低撞号概率，真正的唯一性由 meeting_no 唯一约束 +
    create_meeting 的冲突重试共同保证（仅靠查重存在 TOCTOU 竞态）。
    """
    for _ in range(100):
        no = _random_meeting_no()
        if not db.query(Meeting).filter(Meeting.meeting_no == no).first():
            return no
    # 极端情况：号段几乎用尽，继续重试直到找到未使用的
    while True:
        no = _random_meeting_no()
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


def is_meeting_member(db: Session, meeting: Meeting, user_id: int) -> bool:
    """判断用户是否属于该会议（创建者 / 主持人 / 联席主持 / 有过参会记录）

    用于所有「读侧」接口的越权防护：会议内容（聊天史、参会名单、录制列表）
    只对会议相关人员开放，任何登录用户不得凭会议号读取。
    """
    if meeting.creator_id == user_id:
        return True
    if user_id in (meeting.host_id, meeting.co_host_id):
        return True
    return db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == user_id
    ).first() is not None


def get_meeting_participant(db: Session, meeting_id: int, participant_id: int):
    """按「会议归属」查询参会者，杜绝跨会议操作他人会议的 participant 记录

    所有以 target_id 为入参的主持人操作都必须经由此函数取目标，
    否则会议 A 的主持人可对会议 B 的参会者执行踢人/禁言/转移主持。
    """
    if not participant_id:
        return None
    return db.query(Participant).filter(
        Participant.id == participant_id,
        Participant.meeting_id == meeting_id
    ).first()


# ============ REST API ============

@router.post("/meetings")
async def create_meeting(
    request: CreateMeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """创建会议"""
    status = "ongoing"
    scheduled_at_dt = None
    if request.scheduled_at:
        # 预约会议，此时状态是 scheduled，未开始
        status = "scheduled"
        scheduled_at_dt = datetime.fromisoformat(request.scheduled_at)

    password_hash = None
    if request.password and request.password.strip():
        # bcrypt 是 CPU 密集的同步调用（单次数百毫秒），直接在 async 端点里执行会
        # 阻塞整个事件循环，使并发的其它请求（含登录）全部排队，必须丢到线程池
        password_hash = await run_in_threadpool(get_password_hash, request.password)

    # 会议号唯一性由「唯一约束 + 冲突重试」保证：
    # generate_meeting_no 的查重是「先查后插」，两个并发创建可能选到同一号码，
    # 后提交者会撞唯一约束；若不重试就会把 500 直接抛给前端。
    meeting = None
    for _ in range(MAX_MEETING_NO_RETRY):
        candidate = Meeting(
            meeting_no=generate_meeting_no(db),
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
        db.add(candidate)
        try:
            db.commit()
            meeting = candidate
            break
        except IntegrityError:
            db.rollback()  # 号码被并发抢占，换一个再试
    if meeting is None:
        return JSONResponse(status_code=503, content=error(503, "会议号分配失败，请稍后重试"))

    db.refresh(meeting)
    meeting_no = meeting.meeting_no

    participant = Participant(
        meeting_id=meeting.id,
        user_id=current_user.id,
        display_name=current_user.display_name,
        admitted=True,  # 创建者无需经过等候室
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
    http_request: Request,
    request: JoinMeetingRequest = JoinMeetingRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """加入会议"""
    # 会议号枚举防护：/join 是探测「某号码是否存在」最直接的入口，
    # 6 位号空间仅 90 万，不限流即可在可接受时间内被遍历出全部有效会议。
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not rate_limit.hit(f"join:user:{current_user.id}", JOIN_LIMIT_PER_USER, RATE_WINDOW) \
            or not rate_limit.hit(f"join:ip:{client_ip}", JOIN_LIMIT_PER_IP, RATE_WINDOW):
        return JSONResponse(status_code=429, content=error(429, "操作过于频繁，请稍后再试"))

    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    if meeting.status == "ended" and meeting.ended_at is not None:
        return JSONResponse(status_code=409, content=error(409, "会议已结束"))

    # 被踢出 / 等候室被拒的用户，会议结束前不得再次加入：
    # 否则「踢人」只是断一次连接，刷新页面重新调用 join 即可再次入会
    banned = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == current_user.id,
        Participant.status.in_(["kicked", "rejected"])
    ).first()
    # 创建者 / 主持人 / 联席主持例外：主持人被转移主持权后仍应能回到会议
    if banned and current_user.id not in (meeting.creator_id, meeting.host_id, meeting.co_host_id):
        return JSONResponse(status_code=403, content=error(403, "你已被移出该会议，无法再次加入"))

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

    # 准入闸门：锁定 / 密码 / 白名单
    # 这三项对「断线重连」同样生效 —— 它们都是主持人可随时收紧的准入控制。
    # 若重连一律放行，就等于「先合法入会一次，之后每次刷新都从重连分支直入」，
    # 主持人后来锁会、改密码、清空白名单都拦不住。
    is_privileged = current_user.id in [meeting.creator_id, meeting.host_id, meeting.co_host_id]

    # 锁定会议：新成员与重连者均无法加入（主持人/创建者/联席主持除外）
    if meeting.locked and not is_privileged:
        return JSONResponse(status_code=403, content=error(403, "会议已锁定，无法加入"))

    # 会议密码：新加入与重连都要校验
    if meeting.password_hash:
        if not request.password:
            return JSONResponse(status_code=403, content=error(403, "需要会议密码"))
        # bcrypt 校验同样是 CPU 密集的同步调用，必须在线程池里执行，
        # 否则并发入会时会阻塞事件循环，拖垮整个后端
        if not await run_in_threadpool(verify_password, request.password, meeting.password_hash):
            return JSONResponse(status_code=403, content=error(403, "会议密码错误"))

    # 白名单：新加入与重连都要校验
    if meeting.whitelist_enabled:
        in_whitelist = db.query(MeetingWhitelist).filter(
            MeetingWhitelist.meeting_id == meeting.id,
            MeetingWhitelist.user_id == current_user.id
        ).first()
        if not in_whitelist:
            return JSONResponse(status_code=403, content=error(403, "你不在会议白名单中"))

    # 检查是否断线重连（之前离开但未被踢/拒，刷新页面或网络恢复）
    reconnecting = db.query(Participant).filter(
        Participant.meeting_id == meeting.id,
        Participant.user_id == current_user.id,
        Participant.left_at != None,
        Participant.status.in_(["joined", "left", "waiting"])
    ).first()
    if reconnecting:
        # 人数硬上限闸门（与新增入会共用同一套原子条件自增）：
        # 断线重连同样会重新占用一个名额，若不设防，重连就成了绕过上限的入口。
        # 条件 participant_count < 上限 写在 UPDATE 里，由 SQLite 写锁串行化，
        # 并发请求不可能同时挤进最后一个名额。
        max_participants = load_max_participants()
        restored = db.query(Meeting).filter(
            Meeting.id == meeting.id,
            Meeting.participant_count < max_participants
        ).update(
            {"participant_count": Meeting.participant_count + 1},
            synchronize_session=False
        )
        if restored == 0:
            db.rollback()
            return JSONResponse(status_code=409, content=error(409, f"会议人数已达上限（{max_participants} 人），无法加入"))
        reconnecting.left_at = None
        # 等候室模式下，只有「曾被正式准入」(admitted) 的成员才能断线重连直入主会场；
        # 其余一律退回等候室 —— 防止「进等候室 → 主动断开 → 重连」绕过等候室
        if meeting.waiting_room_enabled and not reconnecting.admitted:
            reconnecting.status = "waiting"
        else:
            reconnecting.status = "joined"
        # 重连恢复在线人数，并把因“全部离会”自动结束的会议重新置为进行中
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

    # 等候室模式：新参与者状态为 waiting
    participant_status = "waiting" if meeting.waiting_room_enabled else "joined"

    # 人数硬上限闸门：先原子「占名额」再插入记录。
    # 条件是 participant_count < 上限，超员时 rowcount=0，直接拒绝 ——
    # 并发入会不可能同时读到「还有一个名额」而双双通过（这正是先后写会漏的竞态）。
    max_participants = load_max_participants()
    reserved = db.query(Meeting).filter(
        Meeting.id == meeting.id,
        Meeting.participant_count < max_participants
    ).update(
        {"participant_count": Meeting.participant_count + 1},
        synchronize_session=False
    )
    if reserved == 0:
        db.rollback()
        return JSONResponse(status_code=409, content=error(409, f"会议人数已达上限（{max_participants} 人），无法加入"))

    participant = Participant(
        meeting_id=meeting.id,
        user_id=current_user.id,
        display_name=current_user.display_name,
        status=participant_status,
        admitted=(participant_status != "waiting"),  # 等候室关闭时视为已被准入
        audio_on=True,
        video_on=True
    )
    db.add(participant)
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
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """获取会议信息"""
    # 会议号枚举防护：该接口会区分「会议不存在(404)」与「存在但无权查看(403)」，
    # 是比 /join 更廉价的探测点，必须限流。
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not rate_limit.hit(f"meeting_info:user:{current_user.id}", MEETING_INFO_LIMIT_PER_USER, RATE_WINDOW) \
            or not rate_limit.hit(f"meeting_info:ip:{client_ip}", MEETING_INFO_LIMIT_PER_IP, RATE_WINDOW):
        return JSONResponse(status_code=429, content=error(429, "操作过于频繁，请稍后再试"))

    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        return JSONResponse(status_code=404, content=error(404, "会议不存在"))
    # 越权防护：会议详情（参会名单/公告/锁定状态）仅对会议相关人员开放
    if not is_meeting_member(db, meeting, current_user.id):
        return JSONResponse(status_code=403, content=error(403, "无权查看该会议"))

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
    # 越权防护：群聊历史属于会议内容，非参会人员不得凭会议号读取
    if not is_meeting_member(db, meeting, current_user.id):
        return JSONResponse(status_code=403, content=error(403, "无权查看该会议聊天记录"))

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
    """获取会议列表（仅返回与当前用户相关的进行中/已预约会议）

    越权防护：不再向任意登录用户返回全站会议号清单，否则攻击者可借此
    枚举出所有会议号，再配合 /messages 等接口读取他人会议内容。
    """
    related_ids = {row[0] for row in db.query(Participant.meeting_id).filter(
        Participant.user_id == current_user.id
    ).all()}
    related_ids |= {row[0] for row in db.query(Meeting.id).filter(
        Meeting.creator_id == current_user.id
    ).all()}

    if not related_ids:
        return success({"ongoing": [], "scheduled": []})

    ongoing = db.query(Meeting).filter(
        Meeting.status == "ongoing", Meeting.id.in_(related_ids)
    ).all()
    scheduled = db.query(Meeting).filter(
        Meeting.status == "scheduled", Meeting.id.in_(related_ids)
    ).all()

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

# 录制分片按序组装器：{recording_id: {"expected": 下一个应落盘的分片号, "pending": {分片号: 数据}}}
_chunk_state: Dict[int, dict] = {}
# 分片写入必须串行：多个分片请求会在 await request.body() 处交错，
# 且「配额校验 → 写文件 → 更新游标」一旦分离（先各自读文件大小、再各自落盘），
# 并发请求会同时通过校验把配额冲爆（TOCTOU）。因此校验与落盘统一放进这把锁。
_chunk_lock = threading.Lock()

# 录制写入收口标记：{recording_id: 收口时刻（monotonic）}
# stop_recording 先在此登记（此后该录制一律拒绝新分片），再持锁冲刷缓冲区，
# 最后才把 DB 状态置为 completed —— 这样「已通过状态检查、却在停止之后才走到写入」
# 的在途分片会被拦下，保证 completed 之后不再有字节落盘。
# 标记只需覆盖在途请求这一小段窗口，超过 _CLOSED_TTL 的旧记录可安全回收。
_CLOSED_TTL = 60.0
_CLOSED_MAX_KEYS = 256
_closed_recordings: Dict[int, float] = {}

# 录制目录与配额（导入时校验一次；非法配置会让应用起不来而不是默默放行超大上传）
RECORDINGS_DIR = "recordings"
RECORDING_QUOTA = load_recording_quota()


def _recordings_dir_size() -> int:
    """统计 recordings/ 目录实际占用字节数（全局配额判断用）

    直接扫盘而不是累加 Recording.file_size：磁盘上可能存在未被数据库记录的残留
    文件（写盘成功但后续步骤失败），只有实际占用才是「磁盘会不会被写满」的真凭据。
    """
    total = 0
    try:
        with os.scandir(RECORDINGS_DIR) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                except OSError:
                    continue  # 扫描期间文件被删除等竞态：跳过即可
    except FileNotFoundError:
        return 0
    return total


def _prune_closed_locked() -> None:
    """回收过期的收口标记（调用方必须已持有 _chunk_lock）"""
    if len(_closed_recordings) <= _CLOSED_MAX_KEYS:
        return
    now = time.monotonic()
    for rid in [r for r, t in _closed_recordings.items() if now - t > _CLOSED_TTL]:
        del _closed_recordings[rid]


def _close_recording(recording_id: int, file_path: str) -> None:
    """停止录制前的收口：登记「不再接受分片」，并持锁冲刷缓冲区

    顺序不可交换：必须先登记收口再冲刷。否则新分片会插进冲刷与状态更新之间，
    把已经收尾的 webm 尾部再次写乱（停止后仍落盘的字节）。

    冲刷说明：正常情况下所有分片都已按序落盘，缓冲区为空；仅当中间某个分片
    始终没到达（请求彻底丢失）时缓冲区里才有残留，此时按序号升序追加，尽量保住尾部数据。
    """
    with _chunk_lock:
        _closed_recordings[recording_id] = time.monotonic()
        _prune_closed_locked()

        state = _chunk_state.pop(recording_id, None)
        if not state or not state["pending"]:
            return
        with open(file_path, "ab") as f:
            for seq in sorted(state["pending"]):
                f.write(state["pending"][seq])


def _append_chunk(recording_id: int, file_path: str, seq: Optional[int], chunk: bytes) -> Optional[tuple]:
    """在锁内完成「配额校验 + 落盘」，返回 None 表示成功，否则返回 (状态码, 提示)

    为什么校验与写入必须在同一把锁里：分离时多个并发分片会各自看到「还有空间」
    然后一起落盘，把单场 / 目录配额一起冲爆（TOCTOU）。
    缓冲区中的分片同样计入「已接受字节」——它们已通过校验、迟早会落盘，
    不计数等于给配额开了绕过口子。

    落盘规则（seq 不为 None 时，保证 webm 字节序与录制时序一致）：
      - seq == expected：立即落盘，并继续冲刷缓冲区里后续连续的分片；
      - seq >  expected：先缓存，等缺失的分片到达后再落盘；
      - seq <  expected：重复分片，直接丢弃。
    """
    with _chunk_lock:
        if recording_id in _closed_recordings:
            return (400, "录制已结束")

        state = None
        if seq is not None:
            state = _chunk_state.setdefault(recording_id, {"expected": 0, "pending": {}})
            if seq < state["expected"]:
                return None  # 重复分片：不写、也不占配额

        # 已接受字节 = 磁盘上 + 缓冲区中
        current = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        if state:
            current += sum(len(b) for b in state["pending"].values())

        # 单场配额：本录制已接受字节 + 本片，超过即拒收
        if current + len(chunk) > RECORDING_QUOTA["max_total_bytes"]:
            return (413, "本场录制已达存储上限，请停止录制或清理历史录像")
        # 全局配额：整个 recordings/ 目录的实际占用 + 本片，超过即拒收（防止不断开新会议绕过单场限制）
        if _recordings_dir_size() + len(chunk) > RECORDING_QUOTA["max_storage_bytes"]:
            return (413, "服务器录制存储空间不足，请清理历史录像后重试")

        if state is None:
            # 兼容不带序号的旧客户端：同样持锁顺序追加
            with open(file_path, "ab") as f:
                f.write(chunk)
            return None

        state["pending"][seq] = chunk
        with open(file_path, "ab") as f:
            while state["expected"] in state["pending"]:
                f.write(state["pending"].pop(state["expected"]))
                state["expected"] += 1
        return None


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
    file_path = os.path.join(RECORDINGS_DIR, file_name)

    # 确保录制目录存在
    os.makedirs(RECORDINGS_DIR, exist_ok=True)

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

    # 写入收口：先登记「不再接受分片」并持锁冲刷缓冲区，再置 DB 状态。
    # 顺序反过来的话，已通过状态检查的在途分片会在冲刷之后落盘，把收尾后的文件再写乱。
    _close_recording(recording.id, os.path.join(RECORDINGS_DIR, recording.file_name))

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

    # 分片序号（前端 MediaRecorder 每 1 秒产出一片，序号顺序即录制时序）
    seq_header = request.headers.get("x-chunk-seq")
    seq = None
    if seq_header is not None:
        try:
            seq = int(seq_header)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content=error(400, "分片序号非法"))
        if seq < 0:
            return JSONResponse(status_code=400, content=error(400, "分片序号非法"))

    # ---- 配额校验（写在读取请求体之前：否则巨块已经被读进内存，限制就晚了一步）----
    max_chunk = RECORDING_QUOTA["max_chunk_bytes"]
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            declared = int(declared_length)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content=error(400, "Content-Length 非法"))
        if declared > max_chunk:
            return JSONResponse(status_code=413, content=error(413, "单个录制分片超过大小上限"))

    # 读取原始二进制数据
    chunk = await request.body()
    if not chunk:
        return JSONResponse(status_code=400, content=error(400, "空数据"))
    # 兜底再校验一次实际长度：Content-Length 可能缺失（分块传输）或被伪造
    if len(chunk) > max_chunk:
        return JSONResponse(status_code=413, content=error(413, "单个录制分片超过大小上限"))

    file_path = os.path.join(RECORDINGS_DIR, recording.file_name)

    # 单场 / 目录配额校验与落盘在同一把锁内完成（含收口检查），
    # 避免并发分片各自通过校验后一起超配额落盘
    result = _append_chunk(recording.id, file_path, seq, chunk)
    if result is not None:
        status_code, message = result
        return JSONResponse(status_code=status_code, content=error(status_code, message))

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
    # 越权防护：录制清单仅对会议相关人员开放
    if not is_meeting_member(db, meeting, current_user.id):
        return JSONResponse(status_code=403, content=error(403, "无权查看该会议录制"))

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

# 未在该时限内完成首帧鉴权即断开，避免匿名连接长期占用连接与协程
WS_AUTH_TIMEOUT_SECONDS = 5

# ============ 单连接消息限流（广播放大防护） ============
# 每条 WS 消息都会触发「1 份上行 → N 份下行」的转发或广播：一次 chat 要发给全会议，
# 一次 speaking 要发给除自己外所有人。单连接若不限速，攻击者用很少的上行带宽
# 就能让服务端向 N 个成员各推多份下行，把整场会议打成拥塞。
# 分档依据：前端常态频率远低于下表限额（心跳 30s 一次、说话状态仅翻转时上报），
# 因此正常使用几乎不会被误伤，只有脚本洪泛才会命中。
WS_RATE_LIMITS = {
    "chat": (10, 10.0),        # 群聊：10 条 / 10s
    "whisper": (10, 10.0),     # 私聊
    "recall": (10, 10.0),      # 撤回
    "reaction": (20, 10.0),    # 表情：允许较快连点
    "device": (20, 10.0),      # 设备状态（开关麦 / 摄像头）
    "screen": (20, 10.0),      # 屏幕共享状态
    "speaking": (30, 1.0),     # 语音激励：仅状态翻转时上报，1s 内 30 次足够
    "offer": (60, 10.0),       # SDP 协商：Mesh 下属每成员一条，配额放宽
    "answer": (60, 10.0),
    "ice": (100, 10.0),        # ICE 候选：trickle 批量发送，配额最宽
    "ping": (30, 10.0),        # 心跳（前端 30s 一次）
}
# 未单列的类型（主持人管理指令、未知类型）共用一档：正常操作频率很低
WS_DEFAULT_RATE_LIMIT = (30, 10.0)
# 连续被限流多少次后判定为恶意洪泛，直接断开连接
WS_MAX_VIOLATIONS = 20
# 限流提示的最小发送间隔（秒）：洪泛时避免「提示」本身变成额外的下行负担
WS_RATE_NOTICE_INTERVAL = 1.0


async def close_websocket(websocket: WebSocket, code: int = 1000) -> None:
    """安全关闭 WS：连接可能已断开，重复关闭会抛 RuntimeError"""
    try:
        await websocket.close(code=code)
    except RuntimeError:
        pass


def ws_rate_limit_prefix(meeting_no: str, participant_id: int) -> str:
    """连接的限流键前缀：建键与清理共用，避免两处字符串写歪导致计数不被回收"""
    return f"ws:{meeting_no}:{participant_id}:"


async def _finalize_participant_exit(
    db: Session, meeting: Meeting, participant: Participant,
    meeting_no: str, participant_id: int
) -> None:
    """连接结束后的统一收尾：清理内存连接 + 落库离会状态 + 维护人数与自动结束

    「正常掉线」「主动退会」「限流持续超限被断开」三条路径共用本函数，
    避免同一件事写两遍后两边对 participant_count 的处理出现偏差。
    """
    # 席位是「在线成员里谁在发视频」的实时状态：disconnect 会把此人从
    # active_connections 与 participants_info 一并移除，席位随之释放。
    await manager.disconnect(meeting_no, participant_id)
    # 连接级限流键随连接销毁，避免键随参会记录不断累积
    rate_limit.forget_prefix(ws_rate_limit_prefix(meeting_no, participant_id))

    # 掉线/关闭页面：更新离会信息，保证 participant_count 准确。
    # 必须使用「条件原子 UPDATE」而非「先查后写」：主持人踢人时会先落库
    # status=kicked 并主动关闭本连接，随后才轮到本分支执行；
    # 若这里无条件写回 left，会把 kicked/rejected 覆盖成「掉线」，
    # 被踢用户刷新页面即可走「断线重连」分支重新入会，踢人彻底失效。
    affected = db.query(Participant).filter(
        Participant.id == participant_id,
        Participant.meeting_id == meeting.id,
        Participant.left_at.is_(None),
        Participant.status.notin_(["kicked", "rejected"])
    ).update(
        {"left_at": datetime.now(), "status": "left"},
        synchronize_session=False
    )
    if affected:
        db.query(Meeting).filter(
            Meeting.id == meeting.id,
            Meeting.participant_count > 0
        ).update(
            {"participant_count": Meeting.participant_count - 1},
            synchronize_session=False
        )
        db.commit()
        fresh_meeting = db.query(Meeting).filter(Meeting.id == meeting.id).first()
        if fresh_meeting and (fresh_meeting.participant_count or 0) == 0:
            fresh_meeting.status = "ended"
            db.commit()
        audit_log(db, meeting.id, participant.user_id, "disconnect")


@router.websocket("/ws/{meeting_no}")
async def websocket_endpoint(
    websocket: WebSocket,
    meeting_no: str,
    participant_id: int,
    db: Session = Depends(get_db)
):
    """WebSocket连接（JWT 鉴权，防止伪造 participant_id）

    token 走「连接后首帧」而不是 URL query：URL 会进入 access log、浏览器历史与
    代理记录，一旦落盘即可被复用；这里 accept 后等待
    {"type":"auth","token":...}，校验通过前不下发任何会议数据，超时或校验失败即关闭。
    """
    await websocket.accept()

    # 首帧必须是鉴权帧，且必须在 WS_AUTH_TIMEOUT_SECONDS 内到达
    try:
        first = await asyncio.wait_for(
            websocket.receive_json(), timeout=WS_AUTH_TIMEOUT_SECONDS
        )
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, RuntimeError):
        await close_websocket(websocket, 1008)
        return
    if not isinstance(first, dict) or first.get("type") != "auth":
        await close_websocket(websocket, 1008)
        return

    # 统一走 resolve_user_from_token：除签名/过期校验外，还会比对 token 的 ver
    # 与用户当前 token_version，用户改过密码后此前签发的 token 立即失效
    auth_user = resolve_user_from_token(first.get("token") or "", db)
    if auth_user is None:
        await close_websocket(websocket, 1008)
        return
    user_id = auth_user.id

    meeting = db.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()
    if not meeting:
        await close_websocket(websocket)
        return

    participant = db.query(Participant).filter(Participant.id == participant_id).first()
    if not participant or participant.user_id != user_id:
        await close_websocket(websocket, 1008)
        return

    # 被踢出 / 等候室被拒 / 已离会的参会记录不得再建立连接：
    # 否则知道 participant_id 即可绕过踢人与等候室重新入会
    if participant.status not in ("joined", "waiting") or participant.left_at is not None:
        await close_websocket(websocket, 1008)
        return

    # 人数上限兜底：正常入会已在 /join 的原子闸门处拦截，超编只可能来自
    # 历史数据或异常路径。此处用 > 判断（恰好等于上限必须放行 —— 该参会者
    # 本身就在计数内，用 >= 会把正常成员挡在门外），拒绝超编会议继续建连。
    max_participants = load_max_participants()
    if (meeting.participant_count or 0) > max_participants:
        await close_websocket(websocket, 1008)
        return

    # 主持人（含联席主持）
    is_host = is_meeting_host(meeting, participant.user_id)
    is_waiting = (participant.status == "waiting")

    # 获取用户头像（auth_user 即 token 解析出的用户，已与 participant.user_id 校验一致）
    avatar_url = auth_user.avatar_url or ""

    # 入会即开摄像头同样要过一遍席位仲裁：否则「默认开摄像头」会让所有人
    # 在入会瞬间把席位占满，席位上限形同虚设。拿不到席位的人自动降级为
    # 关视频入会，并单独收到 video_denied 让前端同步开关状态。
    video_seat_limit = load_video_seat_limit()
    initial_video_on = participant.video_on
    video_denied_on_join = False
    if initial_video_on and manager.count_video_on(meeting_no) >= video_seat_limit:
        initial_video_on = False
        video_denied_on_join = True
        participant.video_on = False
        db.commit()

    await manager.connect(
        websocket,
        meeting_no,
        participant_id,
        {
            "name": participant.display_name,
            "audio_on": participant.audio_on,
            "video_on": initial_video_on,
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
        # 席位上限下发：前端据此显示「视频 N/上限」，并在开关视频时才真正申请席位
        await manager.send_personal_message({
            "type": "video_seat",
            "payload": {"limit": video_seat_limit}
        }, meeting_no, participant_id)
        if video_denied_on_join:
            await manager.send_personal_message({
                "type": "video_denied",
                "payload": {
                    "message": f"视频席位已满（{video_seat_limit}/{video_seat_limit}），"
                               "你已以关闭摄像头的方式入会，可稍后手动开启",
                    "limit": video_seat_limit,
                    "used": manager.count_video_on(meeting_no)
                }
            }, meeting_no, participant_id)

    # 主持人收到等候室列表
    if is_host:
        await manager.send_personal_message({
            "type": "waiting_participants",
            "payload": manager.get_waiting_participants(meeting_no)
        }, meeting_no, participant_id)

    # 单连接限流状态：连续超限计数 + 上次提示时间（避免提示本身变成洪泛放大源）
    violations = 0
    last_notice_at = 0.0
    force_closed = False

    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            payload = data.get("payload", {})

            # 单连接消息限流：leave_meeting 必须放行（否则用户将被限流卡住无法退出），
            # 其余类型按分档配额放行，超出即丢弃并回提示；持续超限判定为恶意洪泛后断开。
            if message_type != "leave_meeting":
                limit, window = WS_RATE_LIMITS.get(message_type, WS_DEFAULT_RATE_LIMIT)
                bucket = message_type if message_type in WS_RATE_LIMITS else "other"
                # ICE 候选按「对端」分桶：Mesh 下每人同时维护 N-1 条连接，
                # 若所有连接共用一个连接级配额，16 人规模下仅正常 trickle
                # 就可能突破 100/10s —— 候选人被丢弃会直接卡住协商，
                # 连续超限还会把整条连接（即整个参会者）踢出会议。
                # 只对会议内在册成员分桶：避免伪造 target_id 制造海量限流键。
                if message_type == "ice":
                    ice_target = payload.get("target_id")
                    if ice_target and manager.is_active(meeting_no, ice_target):
                        bucket = f"ice:{ice_target}"
                if not rate_limit.hit(
                    ws_rate_limit_prefix(meeting_no, participant_id) + bucket, limit, window
                ):
                    violations += 1
                    notice_at = time.monotonic()
                    if notice_at - last_notice_at >= WS_RATE_NOTICE_INTERVAL:
                        last_notice_at = notice_at
                        await manager.send_personal_message({
                            "type": "rate_limited",
                            "payload": {
                                "message": "操作过于频繁，已忽略本次请求",
                                "rejected_type": message_type
                            }
                        }, meeting_no, participant_id)
                    if violations >= WS_MAX_VIOLATIONS:
                        await close_websocket(websocket, 1008)
                        force_closed = True
                        break
                    continue
                violations = 0

            # 等候室隔离：等候中的用户只允许心跳与主动退出，
            # 其余消息（群聊 / 私聊 / WebRTC 信令 / 设备状态 / 表情 / 主持人指令）一律静默忽略，
            # 防止其通过信令或群聊绕过等候室进入会议。
            # 状态必须动态读取：被准入后，同一连接应立即恢复全部能力。
            if manager.is_waiting(meeting_no, participant_id) and message_type not in ("ping", "leave_meeting"):
                continue

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
                want_video = payload.get("video_on", True)
                # 视频席位仲裁：只有「从关到开」才需要申请席位，关闭或维持开启一律放行。
                # 统计与状态写入之间没有 await，asyncio 单线程下不会被其它协程插入，
                # 因此多人同时抢最后一个席位时只会有一个通过，其余收到 video_denied。
                if want_video and not manager.is_video_on(meeting_no, participant_id):
                    seat_limit = load_video_seat_limit()
                    seat_used = manager.count_video_on(meeting_no)
                    if seat_used >= seat_limit:
                        await manager.send_personal_message({
                            "type": "video_denied",
                            "payload": {
                                "message": f"视频席位已满（{seat_used}/{seat_limit}），"
                                           "请等其他人关闭摄像头后再试",
                                "limit": seat_limit,
                                "used": seat_used
                            }
                        }, meeting_no, participant_id)
                        continue
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
                target = get_meeting_participant(db, meeting.id, target_id)
                if not target or target.left_at is not None:
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
                target = get_meeting_participant(db, meeting.id, target_id)
                if not target or target.left_at is not None:
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
                target = get_meeting_participant(db, meeting.id, target_id)
                if not target or target.left_at is not None:
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
                        target = get_meeting_participant(db, meeting.id, p["id"])
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
                target_participant = get_meeting_participant(db, meeting.id, target_id)
                # 目标必须是本会议且当前在会成员，否则直接忽略（防止跨会议踢人 / 重复扣减计数）
                if not target_participant or target_participant.left_at is not None:
                    continue
                target_participant.left_at = datetime.now()
                target_participant.status = "kicked"
                # 原子 SQL 自减（带 > 0 守卫）：并发踢人/离会时既不丢更新也不写成负数
                db.query(Meeting).filter(
                    Meeting.id == meeting.id,
                    Meeting.participant_count > 0
                ).update(
                    {"participant_count": Meeting.participant_count - 1},
                    synchronize_session=False
                )
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
                # 只允许准入「本会议且仍在等候室」的成员
                target_participant = get_meeting_participant(db, meeting.id, target_id)
                if not target_participant or target_participant.status != "waiting":
                    continue
                target_participant.status = "joined"
                target_participant.admitted = True  # 记录「曾被正式准入」，供断线重连时判断
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
                # 只允许拒绝「本会议且仍在等候室」的成员，
                # 避免把 reject 当成对已入会成员的静默断线手段
                target_participant = get_meeting_participant(db, meeting.id, target_id)
                if not target_participant or target_participant.status != "waiting":
                    continue
                target_participant.status = "rejected"
                target_participant.left_at = datetime.now()
                # 原子 SQL 自减（带 > 0 守卫）
                db.query(Meeting).filter(
                    Meeting.id == meeting.id,
                    Meeting.participant_count > 0
                ).update(
                    {"participant_count": Meeting.participant_count - 1},
                    synchronize_session=False
                )
                db.commit()
                audit_log(db, meeting.id, participant.user_id, "reject", json.dumps({"target_user_id": target_participant.user_id, "target_name": target_participant.display_name}))
                await manager.reject_participant(meeting_no, target_id)

            elif message_type == "leave_meeting":
                # 用户主动退出会议
                participant.left_at = datetime.now()
                participant.status = "left"
                participant.video_on = False
                # 原子 SQL 自减（带 > 0 守卫）
                db.query(Meeting).filter(
                    Meeting.id == meeting.id,
                    Meeting.participant_count > 0
                ).update(
                    {"participant_count": Meeting.participant_count - 1},
                    synchronize_session=False
                )
                db.commit()
                # 自减后重新查询：仅当在线人数确实归零才自动结束会议
                fresh = db.query(Meeting).filter(Meeting.id == meeting.id).first()
                if fresh and (fresh.participant_count or 0) == 0:
                    fresh.status = "ended"
                    db.commit()
                audit_log(db, meeting.id, participant.user_id, "leave")
                await manager.broadcast_to_meeting(meeting_no, {
                    "type": "user_left",
                    "payload": {"id": participant_id, "name": participant.display_name}
                })
                # 统一走收尾函数，确保断开连接、数据库状态和限流键都被回收
                await _finalize_participant_exit(
                    db, meeting, participant, meeting_no, participant_id
                )
                await close_websocket(websocket, 1000)
                break  # 退出循环，正常关闭连接

            elif message_type in ["offer", "answer", "ice"]:
                target_id = payload.get("target_id")
                if not target_id:
                    continue
                # 信令只允许在「已入会成员」之间转发：等候室用户不能被拉入 P2P 连接
                # （发送侧已由上方等候室白名单拦截，这里拦截接收侧）
                if manager.is_waiting(meeting_no, target_id):
                    continue
                await manager.send_personal_message({
                    "type": message_type,
                    "payload": {
                        "from_id": participant_id,
                        "sdp": payload.get("sdp"),
                        "candidate": payload.get("candidate")
                    }
                }, meeting_no, target_id)

    except WebSocketDisconnect:
        await _finalize_participant_exit(db, meeting, participant, meeting_no, participant_id)

    # 限流持续超限被强制断开：与「正常掉线」走同一套收尾
    if force_closed:
        await _finalize_participant_exit(db, meeting, participant, meeting_no, participant_id)