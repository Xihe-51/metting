"""
WebSocket 连接管理器
管理会议内的 WebSocket 连接和消息广播
"""
from fastapi import WebSocket
from typing import Dict, List
import json


class ConnectionManager:
    """WebSocket 连接管理器"""
    
    # 存储结构: {meeting_no: {participant_id: WebSocket}}
    active_connections: Dict[str, Dict[int, WebSocket]] = {}
    
    # 存储参会者信息: {meeting_no: {participant_id: {name, audio_on, video_on, ...}}}
    participants_info: Dict[str, Dict[int, dict]] = {}
    
    async def connect(self, websocket: WebSocket, meeting_no: str, participant_id: int, participant_info: dict):
        """建立连接"""
        await websocket.accept()
        
        if meeting_no not in self.active_connections:
            self.active_connections[meeting_no] = {}
            self.participants_info[meeting_no] = {}
        
        self.active_connections[meeting_no][participant_id] = websocket
        self.participants_info[meeting_no][participant_id] = participant_info
        
        is_waiting = participant_info.get("status") == "waiting"
        
        if not is_waiting:
            # 正常加入：广播给所有人
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
        else:
            # 等候室：只通知主持人
            await self.broadcast_to_host(meeting_no, {
                "type": "waiting_participants",
                "payload": self.get_waiting_participants(meeting_no)
            })
    
    async def disconnect(self, meeting_no: str, participant_id: int, reason: str = "left"):
        """断开连接"""
        if meeting_no in self.active_connections:
            if participant_id in self.active_connections[meeting_no]:
                del self.active_connections[meeting_no][participant_id]
            if participant_id in self.participants_info[meeting_no]:
                del self.participants_info[meeting_no][participant_id]
            
            # 广播给其他人：有人离开了
            await self.broadcast_to_meeting(meeting_no, {
                "type": "user_left",
                "payload": {
                    "id": participant_id,
                    "reason": reason
                }
            })
            
            # 如果会议没人了，清理
            if len(self.active_connections[meeting_no]) == 0:
                del self.active_connections[meeting_no]
                del self.participants_info[meeting_no]
    
    async def kick_participant(self, meeting_no: str, participant_id: int, kicked_by: str):
        """主持人踢出参会者"""
        # 先告诉被踢的人
        await self.send_personal_message({
            "type": "you_were_kicked",
            "payload": {"by": kicked_by}
        }, meeting_no, participant_id)
        
        # 关闭连接
        if meeting_no in self.active_connections:
            if participant_id in self.active_connections[meeting_no]:
                try:
                    await self.active_connections[meeting_no][participant_id].close()
                except:
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
        """获取参会者名称"""
        if meeting_no in self.participants_info:
            if participant_id in self.participants_info[meeting_no]:
                return self.participants_info[meeting_no][participant_id].get("name", "")
        return ""
    
    async def send_personal_message(self, message: dict, meeting_no: str, participant_id: int):
        """发送个人消息"""
        if meeting_no in self.active_connections:
            if participant_id in self.active_connections[meeting_no]:
                websocket = self.active_connections[meeting_no][participant_id]
                await websocket.send_json(message)
    
    async def broadcast_to_meeting(self, meeting_no: str, message: dict, exclude_id: int = None):
        """广播给会议内所有人"""
        if meeting_no not in self.active_connections:
            return
        
        for participant_id, websocket in self.active_connections[meeting_no].items():
            if exclude_id and participant_id == exclude_id:
                continue
            try:
                await websocket.send_json(message)
            except:
                pass  # 连接可能已断开
    
    def get_participants(self, meeting_no: str) -> List[dict]:
        """获取会议内所有已加入的参会者（不含等候室）"""
        if meeting_no not in self.participants_info:
            return []
        
        result = []
        for pid, info in self.participants_info[meeting_no].items():
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
        if meeting_no not in self.participants_info:
            return []
        
        result = []
        for pid, info in self.participants_info[meeting_no].items():
            if info.get("status") == "waiting":
                result.append({
                    "id": pid,
                    "name": info["name"]
                })
        return result
    
    async def broadcast_to_host(self, meeting_no: str, message: dict):
        """只发送给主持人"""
        if meeting_no not in self.participants_info:
            return
        for pid, info in self.participants_info[meeting_no].items():
            if info.get("is_host"):
                await self.send_personal_message(message, meeting_no, pid)
                break
    
    async def admit_participant(self, meeting_no: str, participant_id: int):
        """主持人准入等候室中的参会者"""
        if meeting_no in self.participants_info:
            if participant_id in self.participants_info[meeting_no]:
                info = self.participants_info[meeting_no][participant_id]
                info["status"] = "joined"
                
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
        return False
    
    async def reject_participant(self, meeting_no: str, participant_id: int):
        """主持人拒绝等候室中的参会者"""
        if meeting_no in self.active_connections:
            if participant_id in self.active_connections[meeting_no]:
                # 告诉被拒绝者
                await self.send_personal_message({
                    "type": "you_are_rejected",
                    "payload": {}
                }, meeting_no, participant_id)
                
                # 关闭连接
                try:
                    await self.active_connections[meeting_no][participant_id].close()
                except:
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


# 全局管理器实例
manager = ConnectionManager()