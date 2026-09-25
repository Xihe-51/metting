"""
WebSocket 连接管理器
管理会议内的 WebSocket 连接和消息广播

安全设计（等候室隔离）：
1. active_connections 只存放「已准入」的参会者连接，所有广播只覆盖这个池，
   等候中的用户天然收不到任何会议内容（群聊/名单/设备状态/信令）。
2. waiting_connections 独立存放等候中的连接，仅能收到与自身相关的
   waiting_room / you_are_admitted / you_are_rejected 消息。
3. participants_info 仍保留全部参会者（含等候），用于名单与状态查询。
"""
from fastapi import WebSocket
from typing import Dict, List


class ConnectionManager:
    """WebSocket 连接管理器"""

    # 存储结构: {meeting_no: {participant_id: WebSocket}}
    # 只存放「已准入」的参会者连接 —— 广播只覆盖这个池
    active_connections: Dict[str, Dict[int, WebSocket]] = {}

    # 等候室连接池: {meeting_no: {participant_id: WebSocket}}
    waiting_connections: Dict[str, Dict[int, WebSocket]] = {}

    # 存储参会者信息: {meeting_no: {participant_id: {name, audio_on, video_on, status, ...}}}
    participants_info: Dict[str, Dict[int, dict]] = {}

    def _find_connection(self, meeting_no: str, participant_id: int):
        """在「主池 + 等候池」中查找连接（个人消息两个池都要能送达）"""
        ws = self.active_connections.get(meeting_no, {}).get(participant_id)
        if ws is not None:
            return ws
        return self.waiting_connections.get(meeting_no, {}).get(participant_id)

    def get_status(self, meeting_no: str, participant_id: int) -> str:
        """实时读取参会者状态（waiting / joined ...）

        必须动态读取，不能依赖连接建立时的快照：等候室用户被准入后，
        同一连接内的后续消息应立即按「已入会」处理。
        """
        info = self.participants_info.get(meeting_no, {}).get(participant_id)
        if not info:
            return ""
        return info.get("status", "")

    def is_waiting(self, meeting_no: str, participant_id: int) -> bool:
        """该参会者当前是否仍处于等候室"""
        return self.get_status(meeting_no, participant_id) == "waiting"

    async def connect(self, websocket: WebSocket, meeting_no: str, participant_id: int, participant_info: dict):
        """建立连接（等候中的用户只进等候池，不进入广播池）

        注意：调用方必须先 await websocket.accept()。WS 鉴权改为「连接后首帧」后，
        accept 由端点负责（端点要先 accept，浏览器才允许发送首帧 auth）。
        """
        self.participants_info.setdefault(meeting_no, {})
        self.participants_info[meeting_no][participant_id] = participant_info

        is_waiting = participant_info.get("status") == "waiting"

        if is_waiting:
            self.waiting_connections.setdefault(meeting_no, {})[participant_id] = websocket
            # 等候室：只通知主持人有人等待，不向会议内广播
            await self.broadcast_to_host(meeting_no, {
                "type": "waiting_participants",
                "payload": self.get_waiting_participants(meeting_no)
            })
        else:
            self.active_connections.setdefault(meeting_no, {})[participant_id] = websocket
            # 正常加入：广播给已入会的所有人
            await self.broadcast_to_meeting(meeting_no, {
                "type": "user_joined",
                "payload": {
                    "id": participant_id,
                    "name": participant_info["name"],
                    "audio_on": participant_info["audio_on"],
                    "video_on": participant_info["video_on"],
                    "is_host": participant_info.get("is_host", False),
                    "avatar_url": participant_info.get("avatar_url", "")
                }
            }, exclude_id=participant_id)

    async def disconnect(self, meeting_no: str, participant_id: int, reason: str = "left"):
        """断开连接（同时清理主池与等候池）"""
        was_in_active = participant_id in self.active_connections.get(meeting_no, {})
        was_in_waiting = participant_id in self.waiting_connections.get(meeting_no, {})

        if was_in_active:
            del self.active_connections[meeting_no][participant_id]
        if was_in_waiting:
            del self.waiting_connections[meeting_no][participant_id]
        if participant_id in self.participants_info.get(meeting_no, {}):
            del self.participants_info[meeting_no][participant_id]

        if not was_in_active and not was_in_waiting:
            # 连接已被踢人/拒绝流程清理过，避免重复广播 user_left
            self._cleanup_room(meeting_no)
            return

        if was_in_active:
            # 只有真正在会议内的成员离开才广播
            await self.broadcast_to_meeting(meeting_no, {
                "type": "user_left",
                "payload": {
                    "id": participant_id,
                    "reason": reason
                }
            })
        else:
            # 等候者离开：刷新主持人的等候列表
            await self.broadcast_to_host(meeting_no, {
                "type": "waiting_participants",
                "payload": self.get_waiting_participants(meeting_no)
            })

        self._cleanup_room(meeting_no)

    def _cleanup_room(self, meeting_no: str):
        """两个连接池都空了才清理房间，避免等候室用户信息被误删"""
        if self.active_connections.get(meeting_no) or self.waiting_connections.get(meeting_no):
            return
        self.active_connections.pop(meeting_no, None)
        self.waiting_connections.pop(meeting_no, None)
        self.participants_info.pop(meeting_no, None)

    async def kick_participant(self, meeting_no: str, participant_id: int, kicked_by: str):
        """主持人踢出参会者"""
        # 先告诉被踢的人
        await self.send_personal_message({
            "type": "you_were_kicked",
            "payload": {"by": kicked_by}
        }, meeting_no, participant_id)

        # 关闭连接
        websocket = self._find_connection(meeting_no, participant_id)
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass

        # 从管理器中移除
        await self.disconnect(meeting_no, participant_id, reason="kicked")

    def is_host(self, meeting_no: str, participant_id: int) -> bool:
        """检查是否是主持人"""
        if meeting_no in self.participants_info:
            if participant_id in self.participants_info[meeting_no]:
                return self.participants_info[meeting_no][participant_id].get("is_host", False)
        return False

    def get_participant_name(self, meeting_no: str, participant_id: int) -> str:
        """获取参会者名称（含等候室用户，私聊需要显示对方名字）"""
        if meeting_no in self.participants_info:
            if participant_id in self.participants_info[meeting_no]:
                return self.participants_info[meeting_no][participant_id].get("name", "")
        return ""

    async def send_personal_message(self, message: dict, meeting_no: str, participant_id: int):
        """发送个人消息（主池 / 等候池均可送达）"""
        websocket = self._find_connection(meeting_no, participant_id)
        if websocket is None:
            return
        try:
            await websocket.send_json(message)
        except Exception:
            pass  # 连接可能已断开

    async def broadcast_to_meeting(self, meeting_no: str, message: dict, exclude_id: int = None):
        """广播给会议内所有人（仅已准入成员）

        必须先对连接表做快照再遍历：循环体内有 await（send_json），挂起期间
        其它协程的 connect/disconnect 会增删同一个 dict，直接遍历会抛
        "RuntimeError: dictionary changed size during iteration"。
        """
        for participant_id, websocket in list(self.active_connections.get(meeting_no, {}).items()):
            if exclude_id and participant_id == exclude_id:
                continue
            try:
                await websocket.send_json(message)
            except Exception:
                pass  # 连接可能已断开；本轮快照内继续，不影响其它成员

    def get_participants(self, meeting_no: str) -> List[dict]:
        """获取会议内所有已加入的参会者（不含等候室）"""
        result = []
        for pid, info in list(self.participants_info.get(meeting_no, {}).items()):
            if info.get("status") == "waiting":
                continue  # 等候室中的人不显示在参与者列表
            result.append({
                "id": pid,
                "name": info["name"],
                "audio_on": info["audio_on"],
                "video_on": info["video_on"],
                "sharing_screen": info.get("sharing_screen", False),
                "is_host": info.get("is_host", False),
                "muted": info.get("muted", False),
                "chat_muted": info.get("chat_muted", False),
                "avatar_url": info.get("avatar_url", "")
            })
        return result

    def get_waiting_participants(self, meeting_no: str) -> List[dict]:
        """获取等候室中的参会者"""
        result = []
        for pid, info in list(self.participants_info.get(meeting_no, {}).items()):
            if info.get("status") == "waiting":
                result.append({
                    "id": pid,
                    "name": info["name"]
                })
        return result

    async def broadcast_to_host(self, meeting_no: str, message: dict):
        """只发送给主持人

        同样要快照遍历：send_personal_message 内含 await，挂起期间
        participants_info 可能被 connect/disconnect 增删。
        """
        for pid, info in list(self.participants_info.get(meeting_no, {}).items()):
            if info.get("is_host"):
                await self.send_personal_message(message, meeting_no, pid)
                break

    async def admit_participant(self, meeting_no: str, participant_id: int):
        """主持人准入等候室中的参会者：把连接从等候池迁入广播池"""
        info = self.participants_info.get(meeting_no, {}).get(participant_id)
        if not info or info.get("status") != "waiting":
            return False

        info["status"] = "joined"

        # 先迁移连接再发消息：迁移后该用户才能正常收发广播与信令
        websocket = self.waiting_connections.get(meeting_no, {}).pop(participant_id, None)
        if websocket is not None:
            self.active_connections.setdefault(meeting_no, {})[participant_id] = websocket

        # 告诉被准入者
        await self.send_personal_message({
            "type": "you_are_admitted",
            "payload": {}
        }, meeting_no, participant_id)

        # 发送参与者列表给被准入者
        await self.send_personal_message({
            "type": "participants_list",
            "payload": self.get_participants(meeting_no)
        }, meeting_no, participant_id)

        # 广播给所有人：新用户加入
        await self.broadcast_to_meeting(meeting_no, {
            "type": "user_joined",
            "payload": {
                "id": participant_id,
                "name": info["name"],
                "audio_on": info["audio_on"],
                "video_on": info["video_on"],
                "is_host": info.get("is_host", False),
                "avatar_url": info.get("avatar_url", "")
            }
        }, exclude_id=participant_id)

        return True

    async def reject_participant(self, meeting_no: str, participant_id: int):
        """主持人拒绝等候室中的参会者"""
        # 告诉被拒绝者
        await self.send_personal_message({
            "type": "you_are_rejected",
            "payload": {}
        }, meeting_no, participant_id)

        # 关闭连接
        websocket = self._find_connection(meeting_no, participant_id)
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass

        # 从管理器中移除
        await self.disconnect(meeting_no, participant_id, reason="rejected")
        return True

    def update_participant_status(self, meeting_no: str, participant_id: int, status: dict):
        """更新参会者状态"""
        if meeting_no in self.participants_info:
            if participant_id in self.participants_info[meeting_no]:
                for key, value in status.items():
                    self.participants_info[meeting_no][participant_id][key] = value

    def is_active(self, meeting_no: str, participant_id: int) -> bool:
        """该参会者当前是否在会议内（已准入且连接仍在）

        与 get_status 的区别：等候室用户 status 也可能是 joined 之前的 waiting，
        唯有 active_connections 里的才是真正在会议内、可收发信令的成员。
        """
        return participant_id in self.active_connections.get(meeting_no, {})

    def is_video_on(self, meeting_no: str, participant_id: int) -> bool:
        """该参会者当前是否已占着视频席位（内存实时状态）"""
        info = self.participants_info.get(meeting_no, {}).get(participant_id, {})
        return bool(info.get("video_on"))

    def count_video_on(self, meeting_no: str) -> int:
        """统计当前占着视频席位的成员数

        - 只统计已准入连接：等候室用户不在会议内，不该占席位；
        - 以内存为准而非数据库：席位是「此刻谁在发视频」的实时状态，
          数据库里的 video_on 会因掉线残留而与实际不一致。
        """
        info_map = self.participants_info.get(meeting_no, {})
        return sum(
            1 for pid in self.active_connections.get(meeting_no, {})
            if info_map.get(pid, {}).get("video_on")
        )


# 全局管理器实例
manager = ConnectionManager()