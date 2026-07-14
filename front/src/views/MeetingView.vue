<template>
  <div class="meeting-page">
    <!-- 顶部栏 -->
    <div class="top-bar">
      <div class="meeting-info">
        <span class="title">{{ meetingTitle || '视频会议' }}</span>
        <span class="no">会议号: {{ meetingNo }}</span>
      </div>
      <div class="top-actions">
        <el-button @click="showParticipants = !showParticipants" size="small">
          <el-icon><User /></el-icon> 参会者 ({{ remotePeers.length + 1 }})
        </el-button>
        <el-button type="danger" @click="handleLeave" size="small">离开会议</el-button>
      </div>
    </div>

    <!-- 主体 -->
    <div class="body">
      <!-- 视频网格 -->
      <div class="video-area">
        <div class="video-grid" :style="{ gridTemplateColumns: gridCols }">
          <!-- 本地视频 -->
          <div class="video-item local">
            <video ref="localVideoRef" autoplay muted playsinline></video>
            <div class="video-label">
              {{ displayName }} (我)
              <span v-if="isHost" class="host-badge">主持人</span>
            </div>
            <div class="device-status">
              <span v-if="!audioOn" class="off">麦克风已关</span>
              <span v-if="!videoOn" class="off">摄像头已关</span>
            </div>
          </div>
          <!-- 远程视频 -->
          <div class="video-item" v-for="p in remotePeers" :key="p.id">
            <video :ref="el => setVideoRef(p.id, el as HTMLVideoElement)" autoplay playsinline></video>
            <div class="video-label">
              {{ p.name }}
              <span v-if="p.isHost" class="host-badge">主持人</span>
            </div>
            <div class="device-status">
              <span v-if="!p.audioOn" class="off">麦克风已关</span>
              <span v-if="!p.videoOn" class="off">摄像头已关</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧面板 -->
      <div class="side-panel">
        <!-- 聊天面板 -->
        <div class="chat-panel">
          <div class="chat-header">聊天</div>
          <div class="chat-messages" ref="chatRef">
            <div class="chat-msg" v-for="(m, i) in messages" :key="i" :class="{ self: m.isSelf }">
              <span class="msg-name">{{ m.name }}</span>
              <span class="msg-text">{{ m.content }}</span>
            </div>
          </div>
          <div class="chat-input">
            <el-input v-model="chatText" placeholder="输入消息..." @keyup.enter="sendChat" size="small" />
            <el-button type="primary" size="small" @click="sendChat">发送</el-button>
          </div>
        </div>

        <!-- 参会者列表 -->
        <div class="participants-panel" v-if="showParticipants">
          <div class="panel-header">
            参会者 ({{ remotePeers.length + 1 }})
            <el-button v-if="isHost" size="small" type="warning" @click="muteAll" style="margin-left: auto;">
              全体静音
            </el-button>
          </div>
          <!-- 自己 -->
          <div class="p-item">
            <span>{{ displayName }} (我) <span v-if="isHost" class="host-badge">主持人</span></span>
            <span class="p-status">
              <span :class="audioOn ? 'on' : 'off'">{{ audioOn ? '麦克风开' : '已静音' }}</span>
            </span>
          </div>
          <!-- 其他人 -->
          <div class="p-item" v-for="p in remotePeers" :key="p.id">
            <span>{{ p.name }} <span v-if="p.isHost" class="host-badge">主持人</span></span>
            <span class="p-status">
              <span :class="p.audioOn ? 'on' : 'off'">{{ p.audioOn ? '麦克风开' : '已静音' }}</span>
              <el-button v-if="isHost" size="small" type="danger" @click="kickUser(p.id)" style="margin-left: 8px;">
                踢出
              </el-button>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部控制栏 -->
    <div class="control-bar">
      <el-button :type="audioOn ? 'primary' : 'danger'" @click="toggleAudio">
        <el-icon><Microphone /></el-icon>
        {{ audioOn ? '麦克风' : '已静音' }}
      </el-button>
      <el-button :type="videoOn ? 'primary' : 'danger'" @click="toggleVideo">
        <el-icon><VideoCamera /></el-icon>
        {{ videoOn ? '摄像头' : '已关闭' }}
      </el-button>
      <el-button :type="sharingScreen ? 'warning' : 'default'" @click="toggleScreen">
        {{ sharingScreen ? '停止共享' : '共享屏幕' }}
      </el-button>
      <el-button v-if="isHost" type="warning" @click="muteAll">全体静音</el-button>
      <el-button v-if="isCreator" type="danger" @click="handleEndMeeting">结束会议</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const API_BASE = 'http://localhost:8000/api/v1'
const route = useRoute()
const router = useRouter()

const meetingNo = route.params.meetingNo as string
const displayName = localStorage.getItem('displayName') || ''

// ===== 状态 =====
const meetingTitle = ref('')
const isCreator = ref(false)
const isHost = ref(false)
const audioOn = ref(true)
const videoOn = ref(true)
const sharingScreen = ref(false)
const chatText = ref('')
const messages = ref<Array<{ name: string; content: string; isSelf: boolean }>>([])
const chatRef = ref<HTMLElement>()
const showParticipants = ref(false)

// WebRTC
const localVideoRef = ref<HTMLVideoElement>()
const localStream = ref<MediaStream | null>(null)
const screenStream = ref<MediaStream | null>(null)
const peerConnections = new Map<number, RTCPeerConnection>()
const videoRefs = new Map<number, HTMLVideoElement>()

interface RemotePeer {
  id: number
  name: string
  audioOn: boolean
  videoOn: boolean
  sharingScreen: boolean
  isHost: boolean
}
const remotePeers = ref<RemotePeer[]>([])

// WebSocket
let ws: WebSocket | null = null
let myParticipantId = 0

const getAuthHeaders = () => ({
  headers: { Authorization: `Bearer ${localStorage.getItem('token')}` }
})

const gridCols = computed(() => {
  const count = remotePeers.value.length + 1
  if (count <= 1) return '1fr'
  if (count <= 4) return 'repeat(2, 1fr)'
  return 'repeat(3, 1fr)'
})

const setVideoRef = (id: number, el: HTMLVideoElement) => {
  if (el) videoRefs.set(id, el)
}

// ===== 加入会议 =====
const joinMeeting = async () => {
  try {
    const res = await axios.post(`${API_BASE}/meetings/${meetingNo}/join`, {}, getAuthHeaders())
    const d = res.data.data
    const urlObj = new URL(d.websocket_url)
    myParticipantId = Number(urlObj.searchParams.get('participant_id'))
    return d.websocket_url
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '加入会议失败')
    router.push('/')
    return null
  }
}

const fetchMeetingInfo = async () => {
  try {
    const res = await axios.get(`${API_BASE}/meetings/${meetingNo}`, getAuthHeaders())
    const d = res.data.data
    meetingTitle.value = d.title || ''
    isCreator.value = d.creator_name === displayName
  } catch { /* 静默 */ }
}

// ===== 本地媒体 =====
const startLocalStream = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
    localStream.value = stream
    if (localVideoRef.value) {
      localVideoRef.value.srcObject = stream
    }
  } catch {
    ElMessage.error('无法访问摄像头/麦克风')
  }
}

// ===== 设备控制 =====
const toggleAudio = () => {
  audioOn.value = !audioOn.value
  if (localStream.value) {
    localStream.value.getAudioTracks().forEach(t => t.enabled = audioOn.value)
  }
  sendDeviceStatus()
}

const toggleVideo = () => {
  videoOn.value = !videoOn.value
  if (localStream.value) {
    localStream.value.getVideoTracks().forEach(t => t.enabled = videoOn.value)
  }
  sendDeviceStatus()
}

const toggleScreen = async () => {
  if (sharingScreen.value) {
    screenStream.value?.getTracks().forEach(t => t.stop())
    screenStream.value = null
    sharingScreen.value = false
    if (localStream.value) {
      const videoTrack = localStream.value.getVideoTracks()[0]
      peerConnections.forEach(pc => {
        const sender = pc.getSenders().find(s => s.track?.kind === 'video')
        if (sender && videoTrack) sender.replaceTrack(videoTrack)
      })
    }
    sendScreenStatus()
  } else {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true })
      screenStream.value = stream
      sharingScreen.value = true
      const screenTrack = stream.getVideoTracks()[0]
      peerConnections.forEach(pc => {
        const sender = pc.getSenders().find(s => s.track?.kind === 'video')
        if (sender) sender.replaceTrack(screenTrack)
      })
      screenTrack.onended = () => { toggleScreen() }
      sendScreenStatus()
    } catch { /* 用户取消 */ }
  }
}

// ===== 主持人功能 =====
const muteAll = () => {
  ws?.send(JSON.stringify({ type: 'force_mute_all' }))
  ElMessage.success('已发送全体静音')
}

const kickUser = async (targetId: number) => {
  await ElMessageBox.confirm('确定要踢出该参会者吗？', '确认', {
    confirmButtonText: '踢出',
    cancelButtonText: '取消',
    type: 'warning'
  })
  ws?.send(JSON.stringify({
    type: 'kick_user',
    payload: { target_id: targetId }
  }))
}

const sendDeviceStatus = () => {
  ws?.send(JSON.stringify({
    type: 'device',
    payload: { audio_on: audioOn.value, video_on: videoOn.value }
  }))
}

const sendScreenStatus = () => {
  ws?.send(JSON.stringify({
    type: 'screen',
    payload: { sharing: sharingScreen.value }
  }))
}

// ===== WebRTC =====
const createPeerConnection = (remoteId: number): RTCPeerConnection => {
  const pc = new RTCPeerConnection()
  peerConnections.set(remoteId, pc)

  if (localStream.value) {
    localStream.value.getTracks().forEach(track => {
      pc.addTrack(track, localStream.value!)
    })
  }

  pc.onicecandidate = (e) => {
    if (e.candidate) {
      ws?.send(JSON.stringify({
        type: 'ice',
        payload: { target_id: remoteId, candidate: e.candidate }
      }))
    }
  }

  pc.ontrack = (e) => {
    const video = videoRefs.get(remoteId)
    if (video && e.streams[0]) {
      video.srcObject = e.streams[0]
    }
  }

  return pc
}

const createOfferForPeer = async (remoteId: number) => {
  const pc = createPeerConnection(remoteId)
  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)
  ws?.send(JSON.stringify({
    type: 'offer',
    payload: { target_id: remoteId, sdp: pc.localDescription }
  }))
}

const handleOffer = async (fromId: number, sdp: RTCSessionDescriptionInit) => {
  let pc = peerConnections.get(fromId)
  if (!pc) pc = createPeerConnection(fromId)
  await pc.setRemoteDescription(new RTCSessionDescription(sdp))
  const answer = await pc.createAnswer()
  await pc.setLocalDescription(answer)
  ws?.send(JSON.stringify({
    type: 'answer',
    payload: { target_id: fromId, sdp: pc.localDescription }
  }))
}

const handleAnswer = async (fromId: number, sdp: RTCSessionDescriptionInit) => {
  const pc = peerConnections.get(fromId)
  if (pc) await pc.setRemoteDescription(new RTCSessionDescription(sdp))
}

const handleIce = async (fromId: number, candidate: RTCIceCandidateInit) => {
  const pc = peerConnections.get(fromId)
  if (pc) await pc.addIceCandidate(new RTCIceCandidate(candidate))
}

const closePeerConnection = (remoteId: number) => {
  peerConnections.get(remoteId)?.close()
  peerConnections.delete(remoteId)
}

// ===== WebSocket =====
const connectWebSocket = (wsUrl: string) => {
  ws = new WebSocket(wsUrl)

  ws.onopen = () => {
    console.log('WebSocket 已连接')
    setInterval(() => {
      ws?.send(JSON.stringify({ type: 'ping' }))
    }, 30000)
  }

  ws.onmessage = async (e) => {
    const msg = JSON.parse(e.data)
    const { type, payload } = msg

    switch (type) {
      case 'participants_list': {
        const list = payload as Array<Record<string, any>>
        const others = list.filter(p => p.id !== myParticipantId)
        remotePeers.value = others.map(p => ({
          id: p.id, name: p.name,
          audioOn: p.audio_on, videoOn: p.video_on,
          sharingScreen: p.sharing_screen || false,
          isHost: p.is_host || false
        }))
        // 自己是主持人?
        const me = list.find(p => p.id === myParticipantId)
        if (me) isHost.value = !!me.is_host
        for (const p of remotePeers.value) {
          await createOfferForPeer(p.id)
        }
        break
      }

      case 'user_joined':
        remotePeers.value.push({
          id: payload.id,
          name: payload.name,
          audioOn: payload.audio_on,
          videoOn: payload.video_on,
          sharingScreen: false,
          isHost: payload.is_host || false
        })
        addChatMessage('系统', `${payload.name} 加入了会议`, false)
        break

      case 'user_left':
        remotePeers.value = remotePeers.value.filter(p => p.id !== payload.id)
        closePeerConnection(payload.id)
        const reason = payload.reason === 'kicked' ? '被主持人移出' : '离开了会议'
        addChatMessage('系统', `有人${reason}`, false)
        break

      case 'chat':
        addChatMessage(payload.from_name, payload.content, payload.from_id === myParticipantId)
        break

      case 'device': {
        if (payload.all_mute) {
          // 全体静音通知，更新所有远程参会者
          remotePeers.value.forEach(p => p.audioOn = false)
        } else {
          const peer = remotePeers.value.find(p => p.id === payload.user_id)
          if (peer) {
            peer.audioOn = payload.audio_on
            peer.videoOn = payload.video_on
          }
        }
        break
      }

      case 'force_mute':
        // 被主持人静音
        audioOn.value = false
        if (localStream.value) {
          localStream.value.getAudioTracks().forEach(t => t.enabled = false)
        }
        sendDeviceStatus()
        ElMessage.warning(`主持人 ${payload.by} 已将你静音`)
        break

      case 'you_were_kicked':
        ElMessage.error(`你被主持人 ${payload.by} 移出了会议`)
        cleanup()
        router.push('/')
        break

      case 'offer':
        await handleOffer(payload.from_id, payload.sdp)
        break

      case 'answer':
        await handleAnswer(payload.from_id, payload.sdp)
        break

      case 'ice':
        await handleIce(payload.from_id, payload.candidate)
        break

      case 'meeting_ended':
        ElMessage.warning('会议已结束')
        cleanup()
        router.push('/')
        break

      case 'pong':
        break
    }
  }

  ws.onclose = () => console.log('WebSocket 已断开')
  ws.onerror = (err) => console.error('WebSocket 错误:', err)
}

// ===== 聊天 =====
const addChatMessage = (name: string, content: string, isSelf: boolean) => {
  messages.value.push({ name, content, isSelf })
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

const sendChat = () => {
  const text = chatText.value.trim()
  if (!text) return
  ws?.send(JSON.stringify({ type: 'chat', payload: { content: text } }))
  chatText.value = ''
}

// ===== 离开/结束 =====
const handleLeave = async () => {
  await ElMessageBox.confirm('确定要离开会议吗？', '提示', {
    confirmButtonText: '确定', cancelButtonText: '取消'
  })
  cleanup()
  router.push('/')
}

const handleEndMeeting = async () => {
  await ElMessageBox.confirm('确定要结束会议吗？所有人将被移出。', '警告', {
    confirmButtonText: '结束会议', cancelButtonText: '取消', type: 'warning'
  })
  try {
    await axios.post(`${API_BASE}/meetings/${meetingNo}/end`, {}, getAuthHeaders())
  } catch { /* 静默 */ }
  cleanup()
  router.push('/')
}

const cleanup = () => {
  peerConnections.forEach(pc => pc.close())
  peerConnections.clear()
  localStream.value?.getTracks().forEach(t => t.stop())
  screenStream.value?.getTracks().forEach(t => t.stop())
  ws?.close()
  ws = null
}

// ===== 生命周期 =====
onMounted(async () => {
  await startLocalStream()
  const wsUrl = await joinMeeting()
  if (wsUrl) {
    connectWebSocket(wsUrl)
    fetchMeetingInfo()
  }
})

onUnmounted(() => {
  cleanup()
})
</script>

<style scoped>
.meeting-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #1a1a2e;
  color: #eee;
}

/* 顶部栏 */
.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background: #16213e;
  flex-shrink: 0;
}

.meeting-info .title { font-size: 18px; font-weight: bold; margin-right: 16px; }
.meeting-info .no { color: #aaa; font-size: 13px; }

.top-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

/* 主体 */
.body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.video-area {
  flex: 1;
  padding: 10px;
  overflow-y: auto;
}

.video-grid {
  display: grid;
  gap: 10px;
  height: 100%;
  align-content: center;
}

.video-item {
  position: relative;
  background: #0f3460;
  border-radius: 8px;
  overflow: hidden;
  aspect-ratio: 4/3;
  min-height: 0;
}

.video-item video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.video-item.local video {
  transform: scaleX(-1);
}

.video-label {
  position: absolute;
  bottom: 8px;
  left: 8px;
  background: rgba(0,0,0,0.6);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.host-badge {
  background: #f0ad4e;
  color: #333;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: bold;
}

.device-status {
  position: absolute;
  top: 8px;
  right: 8px;
}

.device-status .off {
  background: rgba(255,0,0,0.7);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  margin-left: 4px;
}

/* 右侧面板 */
.side-panel {
  width: 280px;
  display: flex;
  flex-direction: column;
  background: #16213e;
  border-left: 1px solid #0f3460;
  flex-shrink: 0;
}

/* 聊天面板 */
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-header, .panel-header {
  padding: 12px;
  font-weight: bold;
  border-bottom: 1px solid #0f3460;
  text-align: center;
  display: flex;
  align-items: center;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}

.chat-msg { margin-bottom: 8px; }
.chat-msg.self { text-align: right; }

.msg-name {
  font-size: 11px;
  color: #4ECDC4;
  display: block;
}

.msg-text {
  font-size: 14px;
  display: inline-block;
  background: #0f3460;
  padding: 4px 10px;
  border-radius: 8px;
  margin-top: 2px;
  max-width: 85%;
  word-break: break-all;
}

.chat-msg.self .msg-text { background: #1A535C; }

.chat-input {
  display: flex;
  padding: 10px;
  gap: 8px;
  border-top: 1px solid #0f3460;
}

/* 参会者面板 */
.participants-panel {
  border-top: 1px solid #0f3460;
  max-height: 200px;
  overflow-y: auto;
}

.p-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  border-bottom: 1px solid rgba(255,255,255,0.05);
}

.p-status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.p-status .on { color: #67C23A; }
.p-status .off { color: #F56C6C; }

/* 控制栏 */
.control-bar {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 12px 20px;
  background: #16213e;
  flex-shrink: 0;
}
</style>