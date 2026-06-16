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
    
    # 存储参会者信息: {meeting_no: {participant_id: {name, audio_on, video_on}}}
    participants_info: Dict[str, Dict[int, dict]] = {}
    
    async def connect(self, websocket: WebSocket, meeting_no: str, participant_id: int, participant_info: dict):
        """建立连接"""
        await websocket.accept()
        
        if meeting_no not in self.active_connections:
            self.active_connections[meeting_no] = {}
            self.participants_info[meeting_no] = {}
        
        self.active_connections[meeting_no][participant_id] = websocket
        self.participants_info[meeting_no][participant_id] = participant_info
        
        # 广播给其他人：有人加入了
        await self.broadcast_to_meeting(meeting_no, {
            "type": "user_joined",
            "payload": {
                "id": participant_id,
                "name": participant_info["name"],
                "audio_on": participant_info["audio_on"],
                "video_on": participant_info["video_on"]
            }
        }, exclude_id=participant_id)
    
    async def disconnect(self, meeting_no: str, participant_id: int):
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
                    "reason": "left"
                }
            })
            
            # 如果会议没人了，清理
            if len(self.active_connections[meeting_no]) == 0:
                del self.active_connections[meeting_no]
                del self.participants_info[meeting_no]
    
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
        """获取会议内所有参会者"""
        if meeting_no not in self.participants_info:
            return []
        
        result = []
        for pid, info in self.participants_info[meeting_no].items():
            result.append({
                "id": pid,
                "name": info["name"],
                "audio_on": info["audio_on"],
                "video_on": info["video_on"],
                "sharing_screen": info.get("sharing_screen", False)
            })
        return result
    
    def update_participant_status(self, meeting_no: str, participant_id: int, status: dict):
        """更新参会者状态"""
        if meeting_no in self.participants_info:
            if participant_id in self.participants_info[meeting_no]:
                for key, value in status.items():
                    self.participants_info[meeting_no][participant_id][key] = value


# 全局管理器实例
manager = ConnectionManager()