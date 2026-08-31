<template>
  <div class="meeting-page">
    <!-- 防截屏水印 -->
    <div class="watermark-overlay">
      <div class="watermark-pattern">
        <span v-for="i in 40" :key="i" class="watermark-text">{{ displayName }} · {{ meetingNo }}</span>
      </div>
    </div>

    <!-- 浮动表情 -->
    <div class="floating-reactions">
      <span v-for="r in floatingReactions" :key="r.id" class="float-emoji">{{ r.emoji }}</span>
    </div>

    <!-- 隐藏的画布用于虚拟背景处理 -->
    <canvas ref="bgCanvasRef" class="bg-canvas" width="640" height="480"></canvas>
    <canvas ref="bgProcRef" class="bg-canvas" width="640" height="480"></canvas>
    <!-- 隐藏的画布用于录制合成 -->
    <canvas ref="recCanvasRef" class="rec-canvas" width="1280" height="720"></canvas>

    <!-- 等候室画面 -->
    <div class="waiting-overlay" v-if="inWaitingRoom">
      <div class="waiting-card">
        <div class="waiting-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
          </svg>
        </div>
        <h2 class="waiting-title">等候室</h2>
        <p class="waiting-desc">请稍候，主持人即将邀请你加入会议</p>
        <div class="waiting-dots">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>
        <button class="waiting-btn" @click="handleLeave">离开</button>
      </div>
    </div>

    <!-- 密码输入 -->
    <div class="waiting-overlay" v-if="needPassword">
      <div class="waiting-card">
        <div class="waiting-icon">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="11" width="14" height="11" rx="2"/>
            <path d="M8 11V8a4 4 0 018 0v3"/>
          </svg>
        </div>
        <h2 class="waiting-title">需要会议密码</h2>
        <p class="waiting-desc">请输入密码以加入会议</p>
        <div class="password-row">
          <input
            v-model="joinPassword"
            type="password"
            class="password-input"
            placeholder="输入6位密码"
            maxlength="6"
            @keyup.enter="submitPassword"
          />
          <button class="waiting-btn" @click="submitPassword" :disabled="!joinPassword.trim()">
            加入
          </button>
        </div>
        <p class="password-error" v-if="joinPasswordError">{{ joinPasswordError }}</p>
        <button class="waiting-btn waiting-btn--ghost" @click="router.push('/')">返回首页</button>
      </div>
    </div>

    <!-- 入会前设备预览 -->
    <div class="preview-overlay" v-if="showDevicePreview">
      <div class="preview-card">
        <div class="preview-header">
          <h2 class="preview-title">入会前设备测试</h2>
          <p class="preview-sub">选择摄像头和麦克风，确认设备正常后加入会议</p>
        </div>
        <div class="preview-video-wrap">
          <video v-if="previewReady" ref="previewVideoRef" autoplay muted playsinline class="preview-video"></video>
          <div v-else class="preview-video-placeholder">正在打开摄像头...</div>
        </div>
        <div class="preview-controls">
          <div class="preview-field">
            <label class="preview-label">摄像头</label>
            <select v-model="selectedVideoId" class="preview-select" @change="onDeviceChange">
              <option v-for="d in devices.video" :key="d.deviceId" :value="d.deviceId">{{ d.label || ('摄像头 ' + d.deviceId.slice(0, 4)) }}</option>
            </select>
          </div>
          <div class="preview-field">
            <label class="preview-label">麦克风</label>
            <select v-model="selectedAudioId" class="preview-select" @change="onDeviceChange">
              <option v-for="d in devices.audio" :key="d.deviceId" :value="d.deviceId">{{ d.label || ('麦克风 ' + d.deviceId.slice(0, 4)) }}</option>
            </select>
          </div>
        </div>
        <div class="preview-meter-wrap">
          <span class="preview-label">麦克风音量</span>
          <div class="preview-meter"><div class="preview-meter-fill" :style="{ width: micLevel + '%' }"></div></div>
        </div>
        <div class="preview-actions">
          <button class="preview-btn" @click="skipPreview">跳过</button>
          <button class="preview-btn preview-btn--primary" @click="confirmPreview">加入会议</button>
        </div>
      </div>
    </div>

    <!-- 分享弹窗 -->
    <div class="share-overlay" v-if="showShareDialog" @click.self="showShareDialog = false">
      <div class="share-card">
        <div class="share-header">
          <h2 class="share-title">邀请他人加入会议</h2>
          <button class="share-close" @click="showShareDialog = false">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div class="share-body">
          <div class="share-item">
            <span class="share-label">会议标题</span>
            <span class="share-value">{{ meetingTitle || '视频会议' }}</span>
          </div>
          <div class="share-item">
            <span class="share-label">会议号</span>
            <div class="share-copy-row">
              <span class="share-value share-value--code">{{ meetingNo }}</span>
              <button class="share-copy-btn" @click="copyText(meetingNo, '会议号')">复制</button>
            </div>
          </div>
          <div class="share-item" v-if="meetingPassword">
            <span class="share-label">会议密码</span>
            <div class="share-copy-row">
              <span class="share-value share-value--code">{{ showPassword ? meetingPassword : '••••••' }}</span>
              <div class="share-btn-group">
                <button class="share-copy-btn share-icon-btn" @click="showPassword = !showPassword" :title="showPassword ? '隐藏密码' : '显示密码'">
                  <svg v-if="!showPassword" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                    <circle cx="12" cy="12" r="3"/>
                  </svg>
                  <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/>
                    <line x1="1" y1="1" x2="23" y2="23"/>
                  </svg>
                </button>
                <button class="share-copy-btn" @click="copyText(meetingPassword, '密码')">复制</button>
              </div>
            </div>
          </div>
          <div class="share-item">
            <span class="share-label">邀请链接</span>
            <div class="share-copy-row">
              <span class="share-value share-value--link">{{ inviteLink }}</span>
              <button class="share-copy-btn" @click="copyText(inviteLink, '邀请链接')">复制</button>
            </div>
          </div>
          <button class="share-all-btn" @click="copyAllInfo">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2"/>
              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
            </svg>
            一键复制全部信息
          </button>
          <div class="share-hint">将以上信息发送给参会者即可加入会议</div>
        </div>
      </div>
    </div>

    <!-- 公告/议程弹窗 -->
    <div class="share-overlay announce-overlay" v-if="showAnnouncementDialog" @click.self="showAnnouncementDialog = false">
      <div class="share-card">
        <div class="share-header">
          <h2 class="share-title">房间公告 / 议程</h2>
          <button class="share-close" @click="showAnnouncementDialog = false">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div class="share-body">
          <textarea class="announce-input" v-model="announcementDraft" rows="5" placeholder="输入公告或议程内容，所有人入会即可看到"></textarea>
          <div class="announce-actions">
            <button class="announce-btn" @click="showAnnouncementDialog = false">取消</button>
            <button class="announce-btn announce-btn--primary" @click="saveAnnouncement">保存公告</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 顶部栏 -->
    <header class="top-bar">
      <div class="meeting-info">
        <span class="title">{{ meetingTitle || '视频会议' }}</span>
        <span class="meeting-no">#{{ meetingNo }}</span>
      </div>
      <div class="top-actions">
        <button class="btn btn-sm" @click="showShareDialog = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="18" cy="5" r="3"/>
            <circle cx="6" cy="12" r="3"/>
            <circle cx="18" cy="19" r="3"/>
            <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
            <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
          </svg>
          分享
        </button>
        <button v-if="isHost" class="btn btn-sm" :class="{ 'btn-warn': meetingLocked }" @click="toggleLock" :title="meetingLocked ? '解锁会议' : '锁定会议'">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2"/>
            <path d="M7 11V7a5 5 0 0110 0v4"/>
          </svg>
          {{ meetingLocked ? '解锁' : '锁定' }}
        </button>
        <button v-if="isHost" class="btn btn-sm" @click="openAnnouncementDialog" title="设置房间公告">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="16" x2="12" y2="12"/>
            <line x1="12" y1="8" x2="12.01" y2="8"/>
          </svg>
          公告
        </button>
        <button class="btn btn-sm" @click="showParticipants = !showParticipants">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="8" r="4"/>
            <path d="M4 20c0-4 4-7 8-7s8 3 8 7"/>
          </svg>
          {{ remotePeers.length + 1 }}
        </button>
        <button class="btn btn-sm btn-danger" @click="handleLeave">离开</button>
      </div>
    </header>

    <!-- 房间公告/议程 -->
    <div class="announcement-bar" v-if="announcement">
      <span class="announcement-label">公告</span>
      <span class="announcement-text">{{ announcement }}</span>
    </div>

    <!-- 主体 -->
    <div class="body">
      <section class="video-area">
        <div class="video-grid" :style="{ gridTemplateColumns: gridCols }">
          <div class="video-card local">
            <div class="card-media">
              <video ref="localVideoRef" autoplay muted playsinline></video>
              <div class="avatar-placeholder" v-if="!videoOn">
                <img v-if="myAvatarUrl" :src="API_STATIC + myAvatarUrl" class="avatar-img-large" />
                <span v-else class="avatar-letter" :style="{ background: avatarColor(displayName) }">
                  {{ displayName?.charAt(0) || '?' }}
                </span>
              </div>
            </div>
            <div class="card-footer">
              <img v-if="myAvatarUrl" :src="API_STATIC + myAvatarUrl" class="avatar-dot-img" />
              <span v-else class="avatar-dot" :style="{ background: avatarColor(displayName) }">
                {{ displayName?.charAt(0) || '?' }}
              </span>
              <span class="name">{{ displayName }}</span>
              <span v-if="isHost" class="host-badge">主持</span>
              <span class="spacer"></span>
              <span v-if="!audioOn" class="tag-off">静音</span>
              <span v-if="!videoOn" class="tag-off">关摄像头</span>
            </div>
          </div>
          <div class="video-card" :class="{ speaking: p.speaking }" v-for="p in remotePeers" :key="p.id">
            <div class="card-media">
              <video :ref="el => setVideoRef(p.id, el as HTMLVideoElement)" autoplay playsinline></video>
              <div class="avatar-placeholder" v-if="!p.videoOn">
                <img v-if="p.avatarUrl" :src="API_STATIC + p.avatarUrl" class="avatar-img-large" />
                <span v-else class="avatar-letter" :style="{ background: avatarColor(p.name) }">
                  {{ p.name?.charAt(0) || '?' }}
                </span>
              </div>
            </div>
            <div class="card-footer">
              <img v-if="p.avatarUrl" :src="API_STATIC + p.avatarUrl" class="avatar-dot-img" />
              <span v-else class="avatar-dot" :style="{ background: avatarColor(p.name) }">
                {{ p.name?.charAt(0) || '?' }}
              </span>
              <span class="name">{{ p.name }}</span>
              <span v-if="p.isHost" class="host-badge">主持</span>
              <span class="spacer"></span>
              <span v-if="!p.audioOn" class="tag-off">静音</span>
              <span v-if="!p.videoOn" class="tag-off">关摄像头</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 右侧面板 -->
      <aside class="side-panel" :class="{ collapsed: chatCollapsed }">
        <!-- 折叠时的展开按钮 -->
        <button v-if="chatCollapsed" class="panel-expand-btn" @click="toggleChatCollapse" title="展开聊天面板">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 18l-6-6 6-6"/>
          </svg>
        </button>

        <!-- 聊天区 -->
        <div class="chat-area" v-show="!chatCollapsed">
          <div class="chat-top">
            <div class="chat-tabs">
              <button :class="['chat-tab', { active: chatMode === 'all' }]" @click="switchChatMode('all')">全部</button>
              <button :class="['chat-tab', { active: chatMode === 'whisper' }]" @click="switchChatMode('whisper')">
                私聊
                <span v-if="whisperUnread > 0" class="unread-dot">{{ whisperUnread > 99 ? '99+' : whisperUnread }}</span>
              </button>
            </div>
            <button class="panel-collapse-btn" @click="toggleChatCollapse" title="收起聊天面板">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 18l6-6-6-6"/>
              </svg>
            </button>
          </div>
          <div class="whisper-bar" v-if="chatMode === 'whisper'">
            <span class="whisper-label">私聊对象：</span>
            <select v-model="whisperTargetId" class="whisper-select">
              <option :value="0" disabled>选择参会者</option>
              <option v-for="p in remotePeers" :key="p.id" :value="p.id">{{ p.name }}{{ p.isHost ? ' (主持)' : '' }}</option>
            </select>
          </div>
          <div class="chat-messages" ref="chatRef">
            <div class="msg-system" v-for="(m, i) in filteredMessages.filter(x => x.isSystem)" :key="'s'+i">
              {{ m.content }}
            </div>
            
            <div class="msg-row" v-for="(m, i) in filteredMessages.filter(x => !x.isSystem)" :key="'m'+i" :class="{ sent: m.isSelf, whisper: m.isWhisper }">
              <img v-if="!m.isSelf && m.avatarUrl" :src="API_STATIC + m.avatarUrl" class="msg-avatar-img" />
              <div v-else-if="!m.isSelf" class="msg-avatar" :style="{ background: avatarColor(m.name) }">
                {{ m.name?.charAt(0) }}
              </div>
              <div class="msg-body">
                <span class="msg-name" v-if="!m.isSelf">
                  {{ m.name }}
                  <span class="whisper-tag" v-if="m.isWhisper">私聊</span>
                </span>
                <div class="msg-bubble" :class="{ mine: m.isSelf, whisper: m.isWhisper, recalled: m.recalled }">
                  <template v-if="m.recalled">
                    <span class="msg-recalled">消息已撤回</span>
                  </template>
                  <template v-else>
                    <template v-for="(part, pi) in messageParts(m.content)" :key="pi">
                      <span v-if="part.mention" class="msg-mention">{{ part.text }}</span>
                      <template v-else>{{ part.text }}</template>
                    </template>
                  </template>
                </div>
                <div class="msg-meta-line">
                  <button v-if="m.isSelf && !m.isWhisper && !m.recalled && m.id" class="msg-recall-btn" @click="recallMessage(m.id)">撤回</button>
                  <span class="msg-time">{{ formatMsgTime(m.time) }}</span>
                </div>
              </div>
              <img v-if="m.isSelf && m.avatarUrl" :src="API_STATIC + m.avatarUrl" class="msg-avatar-img" />
              <div v-else-if="m.isSelf" class="msg-avatar" :style="{ background: avatarColor(m.name) }">
                {{ m.name?.charAt(0) }}
              </div>
            </div>
          </div>
          <div class="reaction-bar">
            <button v-for="e in ['👍','❤️','😂','🎉','👏','😮']" :key="e" class="reaction-btn" @click="sendReaction(e)">{{ e }}</button>
          </div>
          <div class="chat-input-area">
            <!-- @提及候选人弹层 -->
            <div class="mention-pop" v-if="mentionOpen && mentionCandidates.length > 0">
              <div class="mention-hint">提及参会者</div>
              <button
                v-for="c in mentionCandidates"
                :key="c.isAll ? 'all' : c.id"
                class="mention-item"
                @mousedown.prevent="selectMention(c.name)"
              >
                <span class="mention-name">{{ c.name }}</span>
                <span v-if="c.isAll" class="mention-all">所有人</span>
              </button>
            </div>
            <textarea
              v-model="chatText"
              :placeholder="chatMode === 'whisper' ? '输入私聊消息...' : '输入消息，@ 提及他人...'"
              @keydown.enter.exact.prevent="sendChat"
              @input="onChatInput"
              @blur="mentionOpen = false"
              rows="2"
              ref="inputRef"
            ></textarea>
            <button class="send-btn" @click="sendChat" :disabled="!chatText.trim()">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 2L11 13"/>
                <path d="M22 2l-7 20-4-9-9-4 20-7z"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- 参会者面板 -->
        <div class="participants-area" v-if="showParticipants && !chatCollapsed">
          <div class="panel-header" style="justify-content:space-between">
            <span>参会者 ({{ remotePeers.length + 1 }})</span>
            <button v-if="isHost" class="btn btn-xs btn-warn" @click="muteAll">全体静音</button>
          </div>
          <div class="person-row">
            <img v-if="myAvatarUrl" :src="API_STATIC + myAvatarUrl" class="avatar-dot-img" />
            <span v-else class="avatar-dot" :style="{ background: avatarColor(displayName) }">
              {{ displayName?.charAt(0) }}
            </span>
            <span class="person-name">
              {{ displayName }}
              <span v-if="isHost" class="host-badge">主持</span>
              <span v-if="myMuted" class="mute-badge">禁麦</span>
              <span v-if="myChatMuted" class="mute-badge">禁聊</span>
            </span>
            <span class="person-status" :class="{ on: audioOn }"></span>
          </div>
          <div class="person-row" v-for="p in remotePeers" :key="p.id">
            <img v-if="p.avatarUrl" :src="API_STATIC + p.avatarUrl" class="avatar-dot-img" />
            <span v-else class="avatar-dot" :style="{ background: avatarColor(p.name) }">
              {{ p.name?.charAt(0) }}
            </span>
            <span class="person-name">
              {{ p.name }}
              <span v-if="p.isHost" class="host-badge">主持</span>
              <span v-if="p.muted" class="mute-badge">禁麦</span>
              <span v-if="p.chatMuted" class="mute-badge">禁聊</span>
            </span>
            <span class="person-status" :class="{ on: p.audioOn }"></span>
            <button v-if="isHost" class="btn btn-xs" @click="muteMember(p.id, !p.muted, null)">{{ p.muted ? '解禁麦' : '禁麦' }}</button>
            <button v-if="isHost" class="btn btn-xs" @click="muteMember(p.id, null, !p.chatMuted)">{{ p.chatMuted ? '解禁聊' : '禁聊' }}</button>
            <button v-if="isHost" class="btn btn-xs" @click="setCoHost(p.id, !p.isHost)">{{ p.isHost ? '取消联席' : '设联席' }}</button>
            <button v-if="isHost" class="btn btn-xs" @click="transferHost(p.id)">转主持</button>
            <button v-if="isHost" class="btn btn-xs btn-danger" @click="kickUser(p.id)">移除</button>
            <button v-if="!isHost" class="btn btn-xs btn-accept" @click="sendWhisper(p.id)">私聊</button>
          </div>

          <!-- 等候室 -->
          <div class="waiting-room-panel" v-if="isHost && waitingParticipants.length > 0">
            <div class="panel-header">等候室 ({{ waitingParticipants.length }})</div>
            <div class="person-row" v-for="w in waitingParticipants" :key="w.id">
              <span class="avatar-dot" :style="{ background: avatarColor(w.name) }">
                {{ w.name?.charAt(0) }}
              </span>
              <span class="person-name">{{ w.name }}</span>
              <button class="btn btn-xs btn-accept" @click="admitUser(w.id)">准入</button>
              <button class="btn btn-xs btn-danger" @click="rejectUser(w.id)">拒绝</button>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- 底部控制栏 -->
    <footer class="control-bar">
      <button :class="['ctrl-btn', { on: audioOn, off: !audioOn }]" @click="toggleAudio">
        <template v-if="audioOn">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/>
            <path d="M19 10v2a7 7 0 01-14 0v-2"/>
            <line x1="12" y1="19" x2="12" y2="23"/>
            <line x1="8" y1="23" x2="16" y2="23"/>
          </svg>
        </template>
        <template v-else>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="1" y1="1" x2="23" y2="23"/>
            <path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/>
            <path d="M17 16.95A7 7 0 015.1 9.5M19 10v2"/>
          </svg>
        </template>
        <span>{{ audioOn ? '麦克风' : '已静音' }}</span>
      </button>
      <button :class="['ctrl-btn', { on: videoOn, off: !videoOn }]" @click="toggleVideo">
        <template v-if="videoOn">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="23 7 16 12 23 17 23 7"/>
            <rect x="1" y="5" width="15" height="14" rx="2"/>
          </svg>
        </template>
        <template v-else>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="1" y1="1" x2="23" y2="23"/>
            <path d="M21 15.5V7l-5 5"/>
            <path d="M15 5H5a2 2 0 00-2 2v10a2 2 0 002 2h6"/>
          </svg>
        </template>
        <span>{{ videoOn ? '摄像头' : '已关闭' }}</span>
      </button>
      <div class="bg-picker-wrap">
        <button :class="['ctrl-btn', { on: virtualBg !== 'none' }]" @click="showBgPicker = !showBgPicker">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="2" width="20" height="20" rx="2"/>
            <path d="M12 2v20M2 12h20"/>
          </svg>
          <span>背景</span>
        </button>
        <div class="bg-picker" v-if="showBgPicker">
          <div class="bg-option" :class="{ active: virtualBg === 'none' }" @click="setVirtualBg('none')">无</div>
          <div class="bg-option" :class="{ active: virtualBg === 'blur' }" @click="setVirtualBg('blur')">模糊</div>
          <label class="bg-option bg-upload" :class="{ active: virtualBg === 'image' }" title="选择图片">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            <input type="file" accept="image/*" hidden @change="handleBgImageUpload" />
          </label>
          <div class="bg-option" v-if="virtualBg === 'image'" @click="setVirtualBg('none')" title="清除图片">✕</div>
        </div>
      </div>
      <button :class="['ctrl-btn', { on: sharingScreen }]" @click="toggleScreen">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="3" width="20" height="14" rx="2"/>
          <line x1="8" y1="21" x2="16" y2="21"/>
          <line x1="12" y1="17" x2="12" y2="21"/>
        </svg>
        <span>共享</span>
      </button>
      <button :class="['ctrl-btn', { on: qualityMode !== 'auto' }]" @click="cycleQuality">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/>
          <line x1="8" y1="2" x2="8" y2="18"/>
          <line x1="16" y1="6" x2="16" y2="22"/>
        </svg>
        <span>{{ qualityLabels[qualityMode] }}</span>
      </button>
      <button :class="['ctrl-btn', { on: recording }]" @click="toggleRecording" type="button">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <circle cx="12" cy="12" r="3" :fill="recording ? 'currentColor' : 'none'"/>
        </svg>
        <span>{{ recording ? '停止录制' : '录制' }}</span>
      </button>
      <button v-if="isHost" class="ctrl-btn ctrl-warn" @click="muteAll">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="1" y1="1" x2="23" y2="23"/>
          <path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6"/>
          <path d="M17 16.95A7 7 0 015.1 9.5M19 10v2"/>
        </svg>
        <span>全体静音</span>
      </button>
      <button v-if="isCreator" class="ctrl-btn ctrl-danger" @click="handleEndMeeting">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="15" y1="9" x2="9" y2="15"/>
          <line x1="9" y1="9" x2="15" y2="15"/>
        </svg>
        <span>结束会议</span>
      </button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import axios from 'axios'
import { ImageSegmenter, FilesetResolver } from '@mediapipe/tasks-vision'

const API_BASE = '/api/v1'
const route = useRoute()
const router = useRouter()

const meetingNo = route.params.meetingNo as string
const displayName = localStorage.getItem('displayName') || ''

const meetingTitle = ref('')
const isCreator = ref(false)
const isHost = ref(false)
const audioOn = ref(true)
const videoOn = ref(true)
const sharingScreen = ref(false)
const recording = ref(false)
const virtualBg = ref<'none' | 'blur' | 'image'>('none')
const showBgPicker = ref(false)
const bgCanvasRef = ref<HTMLCanvasElement>()
const bgProcRef = ref<HTMLCanvasElement>()
const bgStream = ref<MediaStream>()
const customBgImage = ref<HTMLImageElement | null>(null)
let bgAnimFrame = 0
let segmenter: ImageSegmenter | null = null
let segmenterReady = false
// 视频画质自适应
type QualityMode = 'auto' | 'low' | 'medium' | 'high'
const qualityMode = ref<QualityMode>('auto')
const qualityLabels: Record<QualityMode, string> = { auto: '自动', low: '流畅', medium: '标清', high: '高清' }
const qualityModes: QualityMode[] = ['auto', 'low', 'medium', 'high']
const qualityConfigs: Record<QualityMode, { width: number; height: number; scaleDown: number; fps: number; bitrate: number }> = {
  auto:   { width: 640, height: 480, scaleDown: 1, fps: 24, bitrate: 800000 },
  low:    { width: 320, height: 240, scaleDown: 2, fps: 15, bitrate: 200000 },
  medium: { width: 640, height: 480, scaleDown: 1, fps: 24, bitrate: 600000 },
  high:   { width: 1280, height: 720, scaleDown: 1, fps: 30, bitrate: 2000000 }
}
let qualityMonitorInterval: number | null = null
const lastBytesSent = new Map<number, number>()
// 录制
const recCanvasRef = ref<HTMLCanvasElement>()
let mediaRecorder: MediaRecorder | null = null
let recAnimFrame = 0
let pendingChunkUploads = new Set<Promise<void>>()
const recordingId = ref<number | null>(null)
const needPassword = ref(false)
const joinPassword = ref('')
const joinPasswordError = ref('')
const chatText = ref('')
const chatMode = ref<'all' | 'whisper'>('all')
const whisperTargetId = ref(0)
const whisperUnread = ref(0)
const chatCollapsed = ref(false)
const showPassword = ref(false)
const mentionOpen = ref(false)
const mentionQuery = ref('')
const messages = ref<Array<{ id?: number; name: string; content: string; isSelf: boolean; isSystem: boolean; isWhisper?: boolean; recalled?: boolean; avatarUrl?: string; time?: string; peerId?: number }>>([])
const chatRef = ref<HTMLElement>()
const inputRef = ref<HTMLTextAreaElement>()
const showParticipants = ref(false)
const showShareDialog = ref(false)
const meetingPassword = ref('')
const inWaitingRoom = ref(false)
const waitingParticipants = ref<Array<{ id: number; name: string }>>([])
const floatingReactions = ref<Array<{ id: number; emoji: string }>>([])
let reactionId = 0

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
  avatarUrl: string
  muted: boolean
  chatMuted: boolean
  speaking: boolean
}
const remotePeers = ref<RemotePeer[]>([])
const myAvatarUrl = ref('')
// 主持人管理 & 房间公告 & 锁定
const announcement = ref('')
const meetingLocked = ref(false)
const showAnnouncementDialog = ref(false)
const announcementDraft = ref('')
// 自己是否被主持人禁言/禁聊
const myMuted = ref(false)
const myChatMuted = ref(false)
// 入会前设备预览
const showDevicePreview = ref(false)
const previewReady = ref(false)
const devices = ref<{ video: MediaDeviceInfo[]; audio: MediaDeviceInfo[] }>({ video: [], audio: [] })
const selectedVideoId = ref('')
const selectedAudioId = ref('')
const previewStream = ref<MediaStream | null>(null)
const previewVideoRef = ref<HTMLVideoElement>()
const micLevel = ref(0)
let micLevelInterval: number | null = null
let previewAudioCtx: AudioContext | null = null
const API_STATIC = window.location.origin
let ws: WebSocket | null = null
let myParticipantId = 0
let pingInterval: number | null = null

const getAuthHeaders = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })

const gridCols = computed(() => {
  const count = remotePeers.value.length + 1
  if (count <= 1) return '1fr'
  if (count <= 4) return 'repeat(2, 1fr)'
  return 'repeat(3, 1fr)'
})

const inviteLink = computed(() => `${window.location.origin}/meeting/${meetingNo}`)

const copyText = async (text: string, label: string) => {
  // 优先使用现代 Clipboard API（仅安全上下文可用）
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      ElMessage.success(`${label}已复制到剪贴板`)
      return
    } catch {
      // 继续走兜底方案
    }
  }
  // 兜底：临时 textarea + execCommand
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    ta.style.top = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    if (ok) {
      ElMessage.success(`${label}已复制到剪贴板`)
    } else {
      ElMessage.error('复制失败，请手动复制')
    }
  } catch {
    ElMessage.error('复制失败，请手动复制')
  }
}

const copyAllInfo = async () => {
  const lines = [
    `${meetingTitle.value || '视频会议'} 会议邀请`,
    `会议号：${meetingNo}`,
  ]
  if (meetingPassword.value) lines.push(`会议密码：${meetingPassword.value}`)
  lines.push(`邀请链接：${inviteLink.value}`)
  await copyText(lines.join('\n'), '会议邀请信息')
}

const formatMsgTime = (iso?: string) => {
  if (!iso) return ''
  const d = new Date(iso)
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

// 将消息内容按 @提及 分割为片段，用于高亮
const messageParts = (content: string) => {
  const parts: Array<{ text: string; mention: boolean }> = []
  const regex = /(@[\u4e00-\u9fa5A-Za-z0-9_]+)/g
  let lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = regex.exec(content)) !== null) {
    const matched = m[1] ?? ''
    if (m.index > lastIndex) parts.push({ text: content.slice(lastIndex, m.index), mention: false })
    parts.push({ text: matched, mention: true })
    lastIndex = m.index + matched.length
  }
  if (lastIndex < content.length) parts.push({ text: content.slice(lastIndex), mention: false })
  return parts
}

// 提及候选人（含「所有人」）
const mentionCandidates = computed(() => {
  const q = mentionQuery.value.trim().toLowerCase()
  const all: Array<{ id: number; name: string; isAll: boolean }> = []
  if (!q || '所有人'.includes(q) || 'all'.includes(q)) all.push({ id: 0, name: '所有人', isAll: true })
  const peers = remotePeers.value
    .filter(p => p.name.toLowerCase().includes(q))
    .map(p => ({ id: p.id, name: p.name, isAll: false }))
  return [...all, ...peers]
})

const onChatInput = () => {
  const cursor = inputRef.value?.selectionStart ?? chatText.value.length
  const before = chatText.value.slice(0, cursor)
  const atIdx = before.lastIndexOf('@')
  if (atIdx !== -1) {
    const afterAt = before.slice(atIdx + 1)
    if (!afterAt.includes(' ') && !afterAt.includes('\n')) {
      mentionOpen.value = true
      mentionQuery.value = afterAt
      return
    }
  }
  mentionOpen.value = false
}

const selectMention = (name: string) => {
  const cursor = inputRef.value?.selectionStart ?? chatText.value.length
  const before = chatText.value.slice(0, cursor)
  const atIdx = before.lastIndexOf('@')
  const newText = before.slice(0, atIdx) + '@' + name + ' ' + chatText.value.slice(cursor)
  chatText.value = newText
  mentionOpen.value = false
  nextTick(() => {
    inputRef.value?.focus()
    const pos = atIdx + name.length + 2
    inputRef.value?.setSelectionRange(pos, pos)
  })
}

const toggleChatCollapse = () => {
  chatCollapsed.value = !chatCollapsed.value
}

const filteredMessages = computed(() => {
  if (chatMode.value === 'whisper') {
    return messages.value.filter(m => m.isWhisper && m.peerId === whisperTargetId.value)
  }
  // 群聊只展示群聊消息与系统消息，排除私聊
  return messages.value.filter(m => !m.isWhisper)
})

const switchChatMode = (mode: 'all' | 'whisper') => {
  chatMode.value = mode
  if (mode === 'whisper') whisperUnread.value = 0
}

const setVideoRef = (id: number, el: HTMLVideoElement) => {
  if (el) videoRefs.set(id, el)
}

const avatarColors = ['#d4a574', '#7eb8c9', '#c9a0dc', '#8cb88c', '#d49b9b', '#9bafd4', '#c9b96e', '#b8a99a']
const avatarColor = (name: string) => {
  let hash = 0
  for (let i = 0; i < (name || '?').length; i++) {
    hash = (hash * 31 + (name || '?').charCodeAt(i)) & 0xffffffff
  }
  return avatarColors[Math.abs(hash) % avatarColors.length]
}

const joinMeeting = async (password = '') => {
  try {
    const res = await axios.post(`${API_BASE}/meetings/${meetingNo}/join`, {
      password
    }, getAuthHeaders())
    const data = res.data.data
    const urlObj = new URL(data.websocket_url)
    myParticipantId = Number(urlObj.searchParams.get('participant_id'))
    inWaitingRoom.value = data.status === 'waiting'
    needPassword.value = false
    joinPasswordError.value = ''
    // 附加 JWT，供 WebSocket 服务端鉴权
    const token = localStorage.getItem('token') || ''
    // 用当前页面访问地址构建 WebSocket，避免局域网下后端返回 localhost 导致连接失败
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${wsProtocol}//${window.location.host}/api/v1/ws/${meetingNo}?participant_id=${myParticipantId}&token=${encodeURIComponent(token)}`
    return wsUrl
  } catch (err: any) {
    const msg = err.response?.data?.message || '加入失败'
    if (msg === '需要会议密码') {
      needPassword.value = true
      return null
    }
    if (msg === '会议密码错误') {
      joinPasswordError.value = '密码错误，请重试'
      joinPassword.value = ''
      return null
    }
    ElMessage.error(msg)
    router.push('/')
    return null
  }
}

const submitPassword = async () => {
  if (!joinPassword.value.trim()) return
  joinPasswordError.value = ''
  const wsUrl = await joinMeeting(joinPassword.value)
  if (wsUrl) {
    connectWebSocket(wsUrl)
    fetchMeetingInfo()
  }
}

const fetchMeetingInfo = async () => {
  try {
    const res = await axios.get(`${API_BASE}/meetings/${meetingNo}`, getAuthHeaders())
    const d = res.data.data
    meetingTitle.value = d.title || ''
    isCreator.value = !!d.is_creator
    isHost.value = !!d.is_host
    announcement.value = d.announcement || ''
    meetingLocked.value = !!d.locked
    if (!meetingPassword.value && d.has_password) {
      // 创建者读本地暂存的明文密码；普通参会者用本次输入的密码
      meetingPassword.value = localStorage.getItem(`meeting_pwd_${meetingNo}`) || joinPassword.value || ''
    }
  } catch {
    /* */
  }
}

const startLocalStream = async () => {
  try {
    const videoConstraints: any = {
      width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 }
    }
    if (selectedVideoId.value) videoConstraints.deviceId = { exact: selectedVideoId.value }
    let audioConstraints: any = true
    if (selectedAudioId.value) audioConstraints = { deviceId: { exact: selectedAudioId.value } }

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: audioConstraints })
    } catch {
      // 麦克风失败时降级为仅视频，避免摄像头被连累无法打开
      stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false })
    }
    localStream.value = stream
    if (localVideoRef.value) localVideoRef.value.srcObject = stream
    initVoiceDetection(stream)
  } catch {
    ElMessage.error('无法访问摄像头')
  }
}

// ===== 语音激励：检测本地音量并广播说话状态 =====
let voiceAnalyser: AnalyserNode | null = null
let voiceDataArray: Uint8Array | null = null
let speakingSent = false
let voiceCheckInterval: number | null = null

const initVoiceDetection = (stream: MediaStream) => {
  try {
    const audioCtx = new AudioContext()
    const source = audioCtx.createMediaStreamSource(stream)
    const analyser = audioCtx.createAnalyser()
    analyser.fftSize = 512
    source.connect(analyser)
    voiceAnalyser = analyser
    voiceDataArray = new Uint8Array(analyser.frequencyBinCount)
    voiceCheckInterval = window.setInterval(() => {
      if (!voiceAnalyser || !voiceDataArray || !ws || ws.readyState !== WebSocket.OPEN) return
      voiceAnalyser.getByteTimeDomainData(voiceDataArray)
      let sum = 0
      for (let i = 0; i < voiceDataArray.length; i++) {
        const v = (voiceDataArray[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / voiceDataArray.length)
      const speaking = rms > 0.05
      if (speaking !== speakingSent) {
        speakingSent = speaking
        ws.send(JSON.stringify({ type: 'speaking', payload: { speaking } }))
      }
    }, 200)
  } catch {
    /* 语音检测失败不影响会议 */
  }
}

const stopVoiceDetection = () => {
  if (voiceCheckInterval) {
    clearInterval(voiceCheckInterval)
    voiceCheckInterval = null
  }
  voiceAnalyser = null
  voiceDataArray = null
}

// ===== 主持人管理：锁定 / 公告 / 转移主持 / 联席主持 / 禁言 =====
const toggleLock = () => {
  if (!isHost.value) return
  const next = !meetingLocked.value
  ws?.send(JSON.stringify({ type: next ? 'lock_meeting' : 'unlock_meeting' }))
}

const openAnnouncementDialog = () => {
  announcementDraft.value = announcement.value
  showAnnouncementDialog.value = true
}

const saveAnnouncement = async () => {
  try {
    await axios.post(`${API_BASE}/meetings/${meetingNo}/announcement`, {
      announcement: announcementDraft.value
    }, getAuthHeaders())
    announcement.value = announcementDraft.value
    showAnnouncementDialog.value = false
    ElMessage.success('公告已更新')
  } catch (err: any) {
    ElMessage.error(err.response?.data?.message || '更新公告失败')
  }
}

const transferHost = async (targetId: number) => {
  try {
    await ElMessageBox.confirm('将主持人身份转移给该成员？你将失去主持人权限。', '转移主持人', {
      confirmButtonText: '确认转移', cancelButtonText: '取消', type: 'warning'
    })
  } catch { return }
  ws?.send(JSON.stringify({ type: 'transfer_host', payload: { target_id: targetId } }))
}

const setCoHost = (targetId: number, enabled: boolean) => {
  ws?.send(JSON.stringify({ type: 'set_co_host', payload: { target_id: targetId, enabled } }))
}

const muteMember = (targetId: number, muteAudio: boolean | null, muteChat: boolean | null) => {
  ws?.send(JSON.stringify({ type: 'mute_user', payload: { target_id: targetId, mute_audio: muteAudio, mute_chat: muteChat } }))
}

// ===== 聊天历史持久化 =====
const fetchChatHistory = async () => {
  try {
    const res = await axios.get(`${API_BASE}/meetings/${meetingNo}/messages`, getAuthHeaders())
    const list = res.data.data || []
    for (const m of list) {
      // 后端按“该消息是否由当前用户发出”返回 is_self，避免断线重连拿到新 participant_id 后归属错位
      const isSelf = !!m.is_self
      const avatarUrl = isSelf ? myAvatarUrl.value : (remotePeers.value.find(p => p.id === m.from_id)?.avatarUrl || '')
      messages.value.push({
        id: m.id,
        name: m.from_name,
        content: m.content,
        isSelf,
        isSystem: false,
        isWhisper: !!m.is_whisper,
        recalled: !!m.recalled,
        avatarUrl,
        time: m.created_at,
        peerId: m.is_whisper ? (isSelf ? m.target_id : m.from_id) : 0
      })
    }
    scrollChat()
  } catch {
    /* */
  }
}

// ===== 入会前设备预览 =====
const enumerateDevices = async () => {
  try {
    const all = await navigator.mediaDevices.enumerateDevices()
    devices.value.video = all.filter(d => d.kind === 'videoinput')
    devices.value.audio = all.filter(d => d.kind === 'audioinput')
    if (!selectedVideoId.value && devices.value.video.length) selectedVideoId.value = devices.value.video[0].deviceId
    if (!selectedAudioId.value && devices.value.audio.length) selectedAudioId.value = devices.value.audio[0].deviceId
  } catch {
    /* */
  }
}

const stopMicLevel = () => {
  if (micLevelInterval) {
    clearInterval(micLevelInterval)
    micLevelInterval = null
  }
  if (previewAudioCtx) {
    previewAudioCtx.close()
    previewAudioCtx = null
  }
}

const startMicLevel = (stream: MediaStream) => {
  stopMicLevel()
  try {
    previewAudioCtx = new AudioContext()
    const src = previewAudioCtx.createMediaStreamSource(stream)
    const analyser = previewAudioCtx.createAnalyser()
    analyser.fftSize = 512
    src.connect(analyser)
    const data = new Uint8Array(analyser.frequencyBinCount)
    micLevelInterval = window.setInterval(() => {
      analyser.getByteTimeDomainData(data)
      let sum = 0
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      micLevel.value = Math.min(100, Math.round(Math.sqrt(sum / data.length) * 300))
    }, 100)
  } catch {
    /* */
  }
}

const startPreview = async () => {
  if (previewStream.value) {
    previewStream.value.getTracks().forEach(t => t.stop())
  }
  try {
    const videoConstraints: any = { width: { ideal: 1280 }, height: { ideal: 720 } }
    if (selectedVideoId.value) videoConstraints.deviceId = { exact: selectedVideoId.value }
    let audioConstraints: any = true
    if (selectedAudioId.value) audioConstraints = { deviceId: { exact: selectedAudioId.value } }

    let stream: MediaStream
    try {
      // 视频 + 麦克风一起请求
      stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: audioConstraints })
    } catch {
      // 麦克风失败时降级为仅视频，保证摄像头能打开
      stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false })
    }
    previewStream.value = stream
    previewReady.value = true
    await nextTick()
    if (previewVideoRef.value) previewVideoRef.value.srcObject = stream
    startMicLevel(stream)
    enumerateDevices() // 授权后刷新设备列表（拿到真实设备名与 deviceId）
  } catch {
    ElMessage.warning('无法打开摄像头，请检查设备或浏览器权限')
  }
}

const onDeviceChange = () => {
  startPreview()
}

const confirmPreview = () => {
  stopMicLevel()
  if (previewStream.value) {
    previewStream.value.getTracks().forEach(t => t.stop())
    previewStream.value = null
  }
  showDevicePreview.value = false
  enterMeeting()
}

const skipPreview = () => {
  stopMicLevel()
  if (previewStream.value) {
    previewStream.value.getTracks().forEach(t => t.stop())
    previewStream.value = null
  }
  showDevicePreview.value = false
  enterMeeting()
}

const enterMeeting = async () => {
  await startLocalStream()
  const wsUrl = await joinMeeting()
  if (wsUrl) {
    connectWebSocket(wsUrl)
    fetchMeetingInfo()
    fetchChatHistory()
  }
}

const toggleAudio = () => {
  if (!audioOn.value && myMuted.value) {
    ElMessage.warning('你已被主持人静音，无法自行开启麦克风')
    return
  }
  audioOn.value = !audioOn.value
  localStream.value?.getAudioTracks().forEach(t => t.enabled = audioOn.value)
  sendDeviceStatus()
}

const toggleVideo = () => {
  videoOn.value = !videoOn.value
  localStream.value?.getVideoTracks().forEach(t => t.enabled = videoOn.value)
  sendDeviceStatus()
}

const setVirtualBg = async (type: 'none' | 'blur' | 'image') => {
  virtualBg.value = type
  showBgPicker.value = false

  if (bgAnimFrame) {
    cancelAnimationFrame(bgAnimFrame)
    bgAnimFrame = 0
  }

  // 恢复原始视频流（远端 + 本地预览）
  const restoreOriginalTrack = () => {
    if (localStream.value) {
      const videoTrack = localStream.value.getVideoTracks()[0]
      if (videoTrack) {
        for (const pc of peerConnections.values()) {
          const sender = pc.getSenders().find(s => s.track?.kind === 'video')
          if (sender) sender.replaceTrack(videoTrack)
        }
      }
      // 恢复本地预览
      if (localVideoRef.value) {
        localVideoRef.value.srcObject = localStream.value
      }
    }
    if (bgStream.value) {
      bgStream.value.getTracks().forEach(t => t.stop())
      bgStream.value = undefined
    }
  }

  if (type === 'none') {
    restoreOriginalTrack()
    return
  }

  if (!localStream.value) {
    ElMessage.warning('请先开启摄像头')
    return
  }
  if (!bgCanvasRef.value) return
  if (type === 'image' && !customBgImage.value) {
    ElMessage.warning('请先选择一张图片')
    return
  }

  const canvas = bgCanvasRef.value
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const W = canvas.width
  const H = canvas.height

  const video = document.createElement('video')
  video.srcObject = localStream.value
  video.autoplay = true
  video.muted = true
  video.playsInline = true
  try {
    await video.play()
  } catch {
    ElMessage.error('无法播放视频流，请检查摄像头权限')
    return
  }

  if (segmenterReady && segmenter && bgProcRef.value) {
    // === 有分割模型：抠图合成 ===
    const procCanvas = bgProcRef.value
    const procCtx = procCanvas.getContext('2d')
    if (!procCtx) return

    const processFrame = () => {
      if (virtualBg.value === 'none' || !segmenter) return

      const result = segmenter.segmentForVideo(video, performance.now())
      const mask = result.confidenceMasks?.[0]
      if (!mask) {
        bgAnimFrame = requestAnimationFrame(processFrame)
        return
      }

      const maskData = mask.getAsFloat32Array()
      const maskW = mask.width
      const maskH = mask.height

      procCanvas.width = maskW
      procCanvas.height = maskH

      procCtx.drawImage(video, 0, 0, maskW, maskH)
      const videoData = procCtx.getImageData(0, 0, maskW, maskH)

      if (type === 'blur') {
        procCtx.filter = 'blur(12px)'
        procCtx.drawImage(video, 0, 0, maskW, maskH)
        procCtx.filter = 'none'
      } else {
        procCtx.drawImage(customBgImage.value!, 0, 0, maskW, maskH)
      }
      const bgData = procCtx.getImageData(0, 0, maskW, maskH)

      const output = procCtx.createImageData(maskW, maskH)
      const vd = videoData.data
      const bd = bgData.data
      const od = output.data
      const pixelCount = maskW * maskH

      for (let i = 0; i < pixelCount; i++) {
        const isPerson = maskData[i] > 0.5
        const p = i * 4
        if (isPerson) {
          od[p] = vd[p]
          od[p + 1] = vd[p + 1]
          od[p + 2] = vd[p + 2]
          od[p + 3] = 255
        } else {
          od[p] = bd[p]
          od[p + 1] = bd[p + 1]
          od[p + 2] = bd[p + 2]
          od[p + 3] = 255
        }
      }

      procCtx.putImageData(output, 0, 0)
      ctx.clearRect(0, 0, W, H)
      ctx.drawImage(procCanvas, 0, 0, W, H)

      bgAnimFrame = requestAnimationFrame(processFrame)
    }

    processFrame()
  } else {
    // === 降级方案：简单叠加（无抠图） ===
    const processFrame = () => {
      if (virtualBg.value === 'none') return
      ctx.clearRect(0, 0, W, H)

      if (type === 'blur') {
        ctx.filter = 'blur(12px)'
        ctx.drawImage(video, 0, 0, W, H)
        ctx.filter = 'none'
        ctx.drawImage(video, 0, 0, W, H)
      } else if (type === 'image' && customBgImage.value) {
        ctx.drawImage(customBgImage.value, 0, 0, W, H)
        ctx.drawImage(video, 0, 0, W, H)
      }

      bgAnimFrame = requestAnimationFrame(processFrame)
    }

    processFrame()
  }

  const newStream = canvas.captureStream(30)
  bgStream.value = newStream
  const newTrack = newStream.getVideoTracks()[0]

  // 替换远端轨道
  for (const pc of peerConnections.values()) {
    const sender = pc.getSenders().find(s => s.track?.kind === 'video')
    if (sender) sender.replaceTrack(newTrack)
  }
  // 本地预览也显示虚拟背景效果
  if (localVideoRef.value) {
    localVideoRef.value.srcObject = newStream
  }
}

const handleBgImageUpload = (e: Event) => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  const img = new Image()
  img.onload = () => {
    customBgImage.value = img
    setVirtualBg('image')
  }
  img.src = URL.createObjectURL(file)
  input.value = '' // 允许重复选同一文件
}

const toggleScreen = async () => {
  if (sharingScreen.value) {
    screenStream.value?.getTracks().forEach(t => t.stop())
    screenStream.value = null
    sharingScreen.value = false
    const vt = localStream.value?.getVideoTracks()[0]
    peerConnections.forEach(pc => {
      const s = pc.getSenders().find(s => s.track?.kind === 'video')
      if (s && vt) s.replaceTrack(vt)
    })
    sendScreenStatus()
  } else {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true })
      screenStream.value = stream
      sharingScreen.value = true
      const st = stream.getVideoTracks()[0]
      peerConnections.forEach(pc => {
        const s = pc.getSenders().find(s => s.track?.kind === 'video')
        if (s) s.replaceTrack(st)
      })
      st.onended = () => {
        toggleScreen()
      }
      sendScreenStatus()
    } catch {
      /* */
    }
  }
}

const toggleRecording = async () => {
  console.log('toggleRecording, recording:', recording.value, 'meetingNo:', meetingNo, 'token:', !!localStorage.getItem('token'))
  if (recording.value) {
    // 停止录制：先等 MediaRecorder 停止并上传完最终分片，再通知后端收尾
    await stopRecorder()
    try {
      if (recordingId.value) {
        await axios.post(`${API_BASE}/meetings/${meetingNo}/recordings/${recordingId.value}/stop`, {}, getAuthHeaders())
      }
      recording.value = false
      recordingId.value = null
      ElMessage.success('录制已停止')
    } catch (err: any) {
      const msg = err.response?.data?.message || err.response?.data?.detail || err.message || '停止录制失败'
      ElMessage.error(msg)
      console.error('停止录制失败:', err)
    }
  } else {
    // 开始录制
    try {
      const res = await axios.post(`${API_BASE}/meetings/${meetingNo}/recordings/start`, {}, getAuthHeaders())
      recordingId.value = res.data.data.recording_id
      recording.value = true
      await startRecorder()
      ElMessage.success('录制已开始')
    } catch (err: any) {
      recording.value = false
      // startRecorder 失败，通知后端取消录制
      if (recordingId.value) {
        try { await axios.post(`${API_BASE}/meetings/${meetingNo}/recordings/${recordingId.value}/stop`, {}, getAuthHeaders()) } catch { /* */ }
      }
      recordingId.value = null
      const msg = err.response?.data?.message || err.response?.data?.detail || err.message || '录制失败'
      ElMessage.error(msg)
      console.error('开始录制失败:', err)
    }
  }
}

const uploadChunk = async (blob: Blob) => {
  if (!recordingId.value) return
  const p = (async () => {
    try {
      const res = await fetch(`${API_BASE}/meetings/${meetingNo}/recordings/${recordingId.value}/chunk`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
          'Content-Type': 'application/octet-stream'
        },
        body: blob
      })
      if (!res.ok) console.warn('录制分片上传失败:', res.status)
    } catch {
      console.warn('录制分片上传网络错误')
    }
  })()
  pendingChunkUploads.add(p)
  try {
    await p
  } finally {
    pendingChunkUploads.delete(p)
  }
}

const startRecorder = async () => {
  const canvas = recCanvasRef.value
  if (!canvas) throw new Error('录制画布未就绪')
  if (!localStream.value) throw new Error('摄像头未开启')

  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('无法获取画布上下文')

  const W = canvas.width
  const H = canvas.height

  // 创建本地视频元素用于绘制
  const localVideo = document.createElement('video')
  localVideo.srcObject = localStream.value
  localVideo.autoplay = true
  localVideo.muted = true
  localVideo.playsInline = true
  try {
    await localVideo.play()
    console.log('录制: 本地视频播放成功')
  } catch {
    throw new Error('无法播放视频流，请确认摄像头权限')
  }

  let frameCount = 0
  const renderFrame = () => {
    if (!mediaRecorder || mediaRecorder.state === 'inactive') return

    ctx.fillStyle = '#1a1a2e'
    ctx.fillRect(0, 0, W, H)

    const videos: HTMLVideoElement[] = [localVideo]
    for (const v of videoRefs.values()) {
      if (v.srcObject) videos.push(v)
    }

    const count = videos.length
    if (count > 0) {
      const cols = Math.ceil(Math.sqrt(count))
      const rows = Math.ceil(count / cols)
      const cellW = W / cols
      const cellH = H / rows
      const pad = 4

      videos.forEach((v, i) => {
        const col = i % cols
        const row = Math.floor(i / cols)
        ctx.drawImage(v, col * cellW + pad, row * cellH + pad, cellW - pad * 2, cellH - pad * 2)
      })
    }

    frameCount++
    if (frameCount % 30 === 0) console.log('录制: 已渲染', frameCount, '帧')

    recAnimFrame = requestAnimationFrame(renderFrame)
  }

  const videoTrack = canvas.captureStream(30).getVideoTracks()[0]
  const audioTrack = localStream.value.getAudioTracks()[0]
  const combined = new MediaStream([videoTrack])
  if (audioTrack) combined.addTrack(audioTrack.clone())

  console.log('录制: canvas流已创建, videoTrack:', videoTrack.readyState, 'audioTrack:', !!audioTrack)

  const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus')
    ? 'video/webm;codecs=vp8,opus'
    : 'video/webm'

  console.log('录制: 使用MIME类型:', mimeType)

  mediaRecorder = new MediaRecorder(combined, { mimeType, videoBitsPerSecond: 2000000 })

  mediaRecorder.ondataavailable = (e) => {
    console.log('录制: dataavailable, size:', e.data.size)
    if (e.data.size > 0) uploadChunk(e.data)
  }

  mediaRecorder.onerror = (e) => {
    console.error('MediaRecorder 错误', e)
    stopRecorder()
  }

  mediaRecorder.onstart = () => console.log('录制: MediaRecorder 已开始')
  mediaRecorder.onstop = () => console.log('录制: MediaRecorder 已停止')

  mediaRecorder.start(1000)
  console.log('录制: 已调用 start(1000), state:', mediaRecorder.state)

  // 必须在 MediaRecorder 创建后再启动渲染循环
  renderFrame()
}

const stopRecorder = async () => {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    // 等待 onstop 触发（最终 dataavailable 分片会在 onstop 之前发出）
    await new Promise<void>((resolve) => {
      mediaRecorder!.onstop = () => resolve()
      mediaRecorder!.stop()
    })
  }
  if (recAnimFrame) {
    cancelAnimationFrame(recAnimFrame)
    recAnimFrame = 0
  }
  mediaRecorder = null
  // 等待所有在途分片（含最终分片）上传完成，避免末尾分片丢失导致 webm 损坏
  if (pendingChunkUploads.size > 0) {
    await Promise.allSettled([...pendingChunkUploads])
  }
}

// ===== 视频画质自适应 =====

type QualityConfig = { width: number; height: number; scaleDown: number; fps: number; bitrate: number }

const applyVideoQuality = async (pc: RTCPeerConnection, config: QualityConfig) => {
  const sender = pc.getSenders().find(s => s.track?.kind === 'video')
  if (!sender) return
  const params = sender.getParameters()
  if (!params.encodings) params.encodings = [{}]
  for (const enc of params.encodings) {
    enc.maxBitrate = config.bitrate
    enc.maxFramerate = config.fps
    enc.scaleResolutionDownBy = config.scaleDown
  }
  try {
    await sender.setParameters(params)
  } catch {
    // 降级：修改 SDP 带宽限制
    try {
      const sdp = pc.localDescription
      if (sdp) {
        const newline = sdp.sdp.includes('\r\n') ? '\r\n' : '\n'
        const modifiedSdp = sdp.sdp.replace(
          /a=mid:video\r?\n/g,
          `a=mid:video${newline}b=AS:${Math.ceil(config.bitrate / 1000)}${newline}`
        )
        await pc.setLocalDescription(new RTCSessionDescription({ type: sdp.type, sdp: modifiedSdp }))
      }
    } catch { /* 降级失败，忽略 */ }
  }
}

const applyQualityToAllPeers = (config: QualityConfig) => {
  for (const pc of peerConnections.values()) {
    applyVideoQuality(pc, config)
  }
}

const changeCameraResolution = async (config: QualityConfig) => {
  const track = localStream.value?.getVideoTracks()[0]
  if (!track) return
  try {
    await track.applyConstraints({
      width: { ideal: config.width },
      height: { ideal: config.height },
      frameRate: { ideal: config.fps }
    })
  } catch {
    // 摄像头不支持该分辨率，用编码器缩放作为降级
    console.warn(`摄像头不支持 ${config.width}x${config.height}`)
  }
}

const cycleQuality = () => {
  const idx = qualityModes.indexOf(qualityMode.value)
  const next = qualityModes[(idx + 1) % qualityModes.length]
  qualityMode.value = next
  const cfg = qualityConfigs[next]
  ElMessage.success(`画质：${qualityLabels[next]}`)
  // 手动模式：直接改摄像头分辨率 + 编码器参数
  changeCameraResolution(cfg)
  applyQualityToAllPeers(cfg)
}

const startQualityMonitor = () => {
  if (qualityMonitorInterval) return
  qualityMonitorInterval = setInterval(async () => {
    if (qualityMode.value !== 'auto') return
    for (const [peerId, pc] of peerConnections) {
      try {
        const stats = await pc.getStats()
        let bytesSent = 0
        let packetsLost = 0
        let rtt = 0
        stats.forEach((report: any) => {
          if (report.type === 'outbound-rtp' && report.kind === 'video') {
            bytesSent = report.bytesSent || 0
            packetsLost = report.packetsLost || 0
          }
          if (report.type === 'candidate-pair' && report.state === 'succeeded') {
            rtt = (report.currentRoundTripTime || 0) * 1000
          }
        })
        const prev = lastBytesSent.get(peerId) || 0
        lastBytesSent.set(peerId, bytesSent)
        // 根据 RTT 和丢包率选择画质
        let config: 'high' | 'medium' | 'low'
        if (rtt > 300 || packetsLost > 20) {
          config = 'low'
        } else if (rtt > 150 || packetsLost > 5) {
          config = 'medium'
        } else {
          config = 'high'
        }
        applyVideoQuality(pc, qualityConfigs[config])
      } catch {
        // 获取统计数据可能失败，忽略
      }
    }
  }, 3000)
}

const stopQualityMonitor = () => {
  if (qualityMonitorInterval) {
    clearInterval(qualityMonitorInterval)
    qualityMonitorInterval = null
  }
  lastBytesSent.clear()
}

const sendReaction = (emoji: string) => {
  ws?.send(JSON.stringify({ type: 'reaction', payload: { emoji } }))
}

const sendWhisper = (targetId: number) => {
  chatMode.value = 'whisper'
  whisperTargetId.value = targetId
  whisperUnread.value = 0
  inputRef.value?.focus()
}

const showFloatingReaction = (emoji: string) => {
  const id = ++reactionId
  floatingReactions.value.push({ id, emoji })
  setTimeout(() => {
    floatingReactions.value = floatingReactions.value.filter(r => r.id !== id)
  }, 2000)
}

const muteAll = () => {
  ws?.send(JSON.stringify({ type: 'force_mute_all' }))
  ElMessage.success('已发送全体静音')
}

const kickUser = async (targetId: number) => {
  await ElMessageBox.confirm('移出该参会者？', '确认', { confirmButtonText: '移出', cancelButtonText: '取消', type: 'warning' })
  ws?.send(JSON.stringify({ type: 'kick_user', payload: { target_id: targetId } }))
}

const admitUser = (targetId: number) => {
  ws?.send(JSON.stringify({ type: 'admit_user', payload: { target_id: targetId } }))
}

const rejectUser = (targetId: number) => {
  ws?.send(JSON.stringify({ type: 'reject_user', payload: { target_id: targetId } }))
}

const sendDeviceStatus = () => {
  ws?.send(JSON.stringify({ type: 'device', payload: { audio_on: audioOn.value, video_on: videoOn.value } }))
}

const sendScreenStatus = () => {
  ws?.send(JSON.stringify({ type: 'screen', payload: { sharing: sharingScreen.value } }))
}

const createPeerConnection = (remoteId: number): RTCPeerConnection => {
  // 配置 STUN 服务器：跨公网/NAT 时通过 STUN 获取公网候选地址打洞；局域网内仍走 host 候选直连
  const pc = new RTCPeerConnection({
    iceServers: [
      { urls: 'stun:stun.l.google.com:19302' },
      { urls: 'stun:stun1.l.google.com:19302' }
    ]
  })
  peerConnections.set(remoteId, pc)
  localStream.value?.getTracks().forEach(t => pc.addTrack(t, localStream.value!))
  // 应用当前画质设置
  applyVideoQuality(pc, qualityConfigs[qualityMode.value])
  pc.onicecandidate = (e) => {
    if (e.candidate) {
      ws?.send(JSON.stringify({ type: 'ice', payload: { target_id: remoteId, candidate: e.candidate } }))
    }
  }
  pc.ontrack = (e) => {
    const v = videoRefs.get(remoteId)
    if (v && e.streams[0]) v.srcObject = e.streams[0]
  }
  return pc
}

const createOfferForPeer = async (remoteId: number) => {
  const pc = createPeerConnection(remoteId)
  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)
  ws?.send(JSON.stringify({ type: 'offer', payload: { target_id: remoteId, sdp: pc.localDescription } }))
}

const handleOffer = async (fromId: number, sdp: RTCSessionDescriptionInit) => {
  let pc = peerConnections.get(fromId)
  if (!pc) pc = createPeerConnection(fromId)
  await pc.setRemoteDescription(new RTCSessionDescription(sdp))
  const answer = await pc.createAnswer()
  await pc.setLocalDescription(answer)
  ws?.send(JSON.stringify({ type: 'answer', payload: { target_id: fromId, sdp: pc.localDescription } }))
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

const mapPeer = (p: any): RemotePeer => {
  const old = remotePeers.value.find(x => x.id === p.id)
  return {
    id: p.id,
    name: p.name,
    audioOn: p.audio_on,
    videoOn: p.video_on,
    sharingScreen: p.sharing_screen || false,
    isHost: p.is_host || false,
    avatarUrl: p.avatar_url || '',
    muted: p.muted || false,
    chatMuted: p.chat_muted || false,
    speaking: old?.speaking || false
  }
}

const connectWebSocket = (wsUrl: string) => {
  ws = new WebSocket(wsUrl)
  ws.onopen = () => {
    startQualityMonitor()
    if (pingInterval) clearInterval(pingInterval)
    pingInterval = window.setInterval(() => {
      ws?.send(JSON.stringify({ type: 'ping' }))
    }, 30000)
  }
  ws.onmessage = async (e) => {
    const msg = JSON.parse(e.data)
    const { type, payload } = msg
    switch (type) {
      case 'participants_list': {
        const list = payload as Array<Record<string, any>>
        const others = list.filter((p: any) => p.id !== myParticipantId)
        remotePeers.value = others.map(mapPeer)
        const me = list.find((p: any) => p.id === myParticipantId)
        if (me) {
          isHost.value = !!me.is_host
          myAvatarUrl.value = me.avatar_url || ''
          myMuted.value = !!me.muted
          myChatMuted.value = !!me.chat_muted
        }
        for (const p of remotePeers.value) {
          await createOfferForPeer(p.id)
        }
        break
      }
      case 'user_joined':
        remotePeers.value.push(mapPeer({
          id: payload.id,
          name: payload.name,
          audio_on: payload.audio_on,
          video_on: payload.video_on,
          is_host: payload.is_host || false,
          avatar_url: payload.avatar_url || ''
        }))
        addSystemMsg(`${payload.name} 加入了`)
        break
      case 'user_left':
        remotePeers.value = remotePeers.value.filter(p => p.id !== payload.id)
        closePeerConnection(payload.id)
        addSystemMsg(payload.reason === 'kicked' ? '有人被移出' : '有人离开了')
        break
      case 'chat': {
        const isSelf = payload.from_id === myParticipantId
        const avatarUrl = isSelf ? myAvatarUrl.value : (remotePeers.value.find(p => p.id === payload.from_id)?.avatarUrl || '')
        addMessage(payload.from_name, payload.content, isSelf, avatarUrl, false, 0, payload.id)
        notifyIfMentioned(payload.content)
        break
      }
      case 'device': {
        if (payload.all_mute) {
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
      case 'message_recalled': {
        const target = messages.value.find(m => m.id === payload.id && !m.isWhisper)
        if (target) {
          target.recalled = true
          target.content = ''
        }
        addSystemMsg(`${payload.from_name} 撤回了一条消息`)
        break
      }
      case 'force_mute':
        audioOn.value = false
        localStream.value?.getAudioTracks().forEach(t => t.enabled = false)
        sendDeviceStatus()
        ElMessage.warning(`主持人已将你静音`)
        break
      case 'you_were_kicked':
        ElMessage.error('你被移出了会议')
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
      case 'waiting_room':
        inWaitingRoom.value = true
        break
      case 'you_are_admitted':
        inWaitingRoom.value = false
        addSystemMsg('主持人已允许你加入会议')
        break
      case 'you_are_rejected':
        ElMessage.warning('主持人拒绝了你的加入请求')
        cleanup()
        router.push('/')
        break
      case 'waiting_participants':
        waitingParticipants.value = payload as Array<{ id: number; name: string }>
        break
      case 'whisper': {
        // 自己发送的回显（发送时已在本地渲染），跳过，避免重复
        if (payload.from_id === myParticipantId) break
        const avatarUrl = remotePeers.value.find(p => p.id === payload.from_id)?.avatarUrl || ''
        addMessage(`${payload.from_name}`, payload.content, false, avatarUrl, true, payload.from_id)
        // 未在读该私聊时，累加未读红点
        if (chatMode.value !== 'whisper' || whisperTargetId.value !== payload.from_id) {
          whisperUnread.value++
        }
        break
      }
      case 'reaction':
        addSystemMsg(`${payload.from_name} ${payload.emoji}`)
        // 浮动表情动画
        showFloatingReaction(payload.emoji)
        break
      case 'announcement':
        announcement.value = payload.announcement || ''
        addSystemMsg(`${payload.by || '主持人'} 更新了公告`)
        break
      case 'meeting_locked':
        meetingLocked.value = !!payload.locked
        addSystemMsg(payload.locked ? `${payload.by} 锁定了会议` : `${payload.by} 解锁了会议`)
        break
      case 'host_changed':
        addSystemMsg(`${payload.by} 将主持人转移给 ${payload.name}`)
        break
      case 'co_host_changed':
        addSystemMsg(payload.enabled ? `${payload.by} 设置 ${payload.name} 为联席主持` : `${payload.by} 取消了 ${payload.name} 的联席主持`)
        break
      case 'mute_status':
        myMuted.value = !!payload.mute_audio
        myChatMuted.value = !!payload.mute_chat
        if (payload.mute_audio) {
          audioOn.value = false
          localStream.value?.getAudioTracks().forEach(t => t.enabled = false)
          sendDeviceStatus()
        }
        if (payload.mute_audio || payload.mute_chat) {
          const parts = [payload.mute_audio ? '静音' : '', payload.mute_chat ? '禁言' : ''].filter(Boolean)
          ElMessage.warning(`你已被主持人${parts.join('并')}`)
        }
        break
      case 'chat_muted':
        ElMessage.warning(payload.message || '你已被禁言')
        break
      case 'speaking': {
        const peer = remotePeers.value.find(p => p.id === payload.user_id)
        if (peer) peer.speaking = !!payload.speaking
        break
      }
      case 'recording_started':
        recording.value = true
        recordingId.value = payload.recording_id
        addSystemMsg(`${payload.by} 开始了录制`)
        break
      case 'recording_stopped':
        recording.value = false
        recordingId.value = null
        addSystemMsg('录制已停止')
        break
    }
  }
}

const addMessage = (name: string, content: string, isSelf: boolean, avatarUrl = '', isWhisper = false, peerId = 0, id?: number) => {
  messages.value.push({ id, name, content, isSelf, isSystem: false, avatarUrl, isWhisper, peerId, time: new Date().toISOString() })
  scrollChat()
}

const addSystemMsg = (content: string) => {
  messages.value.push({ name: '', content, isSelf: false, isSystem: true })
  scrollChat()
}

// 被 @ 提及（含「所有人」）时弹提醒
const notifyIfMentioned = (content: string) => {
  const myName = displayName
  if (!content) return
  const mentionedAll = /@所有人/.test(content)
  const mentionedMe = myName && content.includes(`@${myName}`)
  if (mentionedAll || mentionedMe) {
    ElNotification({
      title: '有人提到了你',
      message: content.length > 40 ? content.slice(0, 40) + '…' : content,
      type: 'info',
      duration: 4000
    })
  }
}

// 撤回自己的群聊消息
const recallMessage = (id?: number) => {
  if (!id) return
  ws?.send(JSON.stringify({ type: 'recall', payload: { message_id: id } }))
}

const scrollChat = () => {
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

const sendChat = () => {
  const text = chatText.value.trim()
  if (!text) return
  if (myChatMuted.value) {
    ElMessage.warning('你已被主持人禁言，无法发送消息')
    return
  }
  if (chatMode.value === 'whisper' && whisperTargetId.value > 0) {
    const target = remotePeers.value.find(p => p.id === whisperTargetId.value)
    ws?.send(JSON.stringify({ type: 'whisper', payload: { target_id: whisperTargetId.value, content: text } }))
    addMessage(`我对 ${target?.name || '未知'}`, text, true, myAvatarUrl.value, true, whisperTargetId.value)
  } else if (chatMode.value === 'whisper') {
    ElMessage.warning('请先选择私聊对象')
    return
  } else {
    ws?.send(JSON.stringify({ type: 'chat', payload: { content: text } }))
  }
  chatText.value = ''
}

const handleLeave = async () => {
  try {
    const isLast = remotePeers.value.length === 0
    const msg = isLast
      ? '你是最后一位参会者，退出后会议将自动结束，确定退出？'
      : '确定离开会议？'
    await ElMessageBox.confirm(msg, '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: isLast ? 'warning' : 'info'
    })
  } catch {
    return // 取消
  }
  ws?.send(JSON.stringify({ type: 'leave_meeting', payload: {} }))
  // 等待服务器关闭连接（break 退出后自动关闭），兜底超时 3 秒
  await new Promise<void>(resolve => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.onclose = () => resolve()
    } else {
      resolve()
    }
    setTimeout(resolve, 3000)
  })
  cleanup()
  router.push('/')
}

const handleEndMeeting = async () => {
  await ElMessageBox.confirm('结束会议？所有人将被移出。', '警告', { confirmButtonText: '结束', cancelButtonText: '取消', type: 'warning' })
  try {
    await axios.post(`${API_BASE}/meetings/${meetingNo}/end`, {}, getAuthHeaders())
  } catch {
    /* */
  }
  cleanup()
  router.push('/')
}

const cleanup = () => {
  stopQualityMonitor()
  stopRecorder()
  stopVoiceDetection()
  stopMicLevel()
  if (pingInterval) {
    clearInterval(pingInterval)
    pingInterval = null
  }
  if (previewStream.value) {
    previewStream.value.getTracks().forEach(t => t.stop())
    previewStream.value = null
  }
  peerConnections.forEach(pc => pc.close())
  peerConnections.clear()
  localStream.value?.getTracks().forEach(t => t.stop())
  screenStream.value?.getTracks().forEach(t => t.stop())
  bgStream.value?.getTracks().forEach(t => t.stop())
  if (bgAnimFrame) cancelAnimationFrame(bgAnimFrame)
  if (customBgImage.value) {
    URL.revokeObjectURL(customBgImage.value.src)
    customBgImage.value = null
  }
  if (segmenter) {
    segmenter.close()
    segmenter = null
    segmenterReady = false
  }
  ws?.close()
  ws = null
}

onMounted(async () => {
  // 先展示入会前设备预览
  await enumerateDevices()
  showDevicePreview.value = true
  startPreview()
  // 后台初始化 MediaPipe 人像分割（本地文件，不走外网 CDN）
  try {
    const vision = await FilesetResolver.forVisionTasks(
      '/mediapipe/'
    )
    segmenter = await ImageSegmenter.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: '/mediapipe/selfie_segmenter.tflite'
      },
      runningMode: 'VIDEO',
      outputCategoryMask: false,
      outputConfidenceMasks: true
    })
    segmenterReady = true
  } catch {
    console.warn('MediaPipe 分割模型加载失败，将使用简单叠加模式')
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
  background: #f5f3ef;
  color: #3a3530;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* 顶部栏 */
.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background: #fff;
  border-bottom: 1px solid #e8e4dc;
  flex-shrink: 0;
}
.meeting-info .title {
  font-size: 16px;
  font-weight: 600;
}
.meeting-info .meeting-no {
  color: #999;
  font-size: 12px;
  margin-left: 10px;
  font-family: monospace;
}
.top-actions {
  display: flex;
  gap: 8px;
}

/* 通用按钮 */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  border: 1px solid #d4cfc6;
  border-radius: 6px;
  background: #fff;
  color: #555;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.btn:hover {
  background: #f0ede8;
}
.btn-sm {
  padding: 4px 10px;
  font-size: 11px;
}
.btn-xs {
  padding: 2px 8px;
  font-size: 10px;
  border-radius: 4px;
}
.btn-danger {
  border-color: #e0afa0;
  color: #c47a6b;
}
.btn-danger:hover {
  background: #fdf0ed;
}
.btn-warn {
  border-color: #d4a853;
  color: #8b6914;
}
.btn-warn:hover {
  background: #fdf8e8;
}

/* 主体 */
.body {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.video-area {
  flex: 1;
  padding: 12px;
  overflow-y: auto;
}
.video-grid {
  display: grid;
  gap: 10px;
  height: 100%;
  align-content: center;
}

/* 视频卡片 */
.video-card {
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  border: 1px solid #e8e4dc;
  display: flex;
  flex-direction: column;
  aspect-ratio: 4/3;
  min-height: 0;
}
.card-media {
  flex: 1;
  position: relative;
  background: #e8e4dc;
  overflow: hidden;
}
.card-media video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.video-card.local .card-media video {
  transform: scaleX(-1);
}
.avatar-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.avatar-letter {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 20px;
  font-weight: 600;
}
.avatar-img-large {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  object-fit: cover;
}
.card-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #fff;
  font-size: 12px;
}
.card-footer .name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-footer .spacer {
  flex: 1;
}
.host-badge {
  font-size: 10px;
  color: #d4a853;
  font-weight: 600;
}
.tag-off {
  font-size: 10px;
  color: #c47a6b;
  background: #fdf0ed;
  padding: 1px 5px;
  border-radius: 3px;
}

/* 小头像 */
.avatar-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
}
.avatar-dot-img {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}

/* 右侧面板 */
.side-panel {
  width: 280px;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-left: 1px solid #e8e4dc;
  flex-shrink: 0;
  transition: width 0.2s ease;
  position: relative;
}
.side-panel.collapsed {
  width: 44px;
}
.panel-expand-btn {
  width: 100%;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: #8C8C8C;
  cursor: pointer;
  transition: color 0.15s;
}
.panel-expand-btn:hover {
  color: #2C2C2C;
}
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.chat-top {
  display: flex;
  align-items: stretch;
  border-bottom: 1px solid #E8E4DE;
}
.chat-top .chat-tabs {
  flex: 1;
  border-bottom: none;
}
.panel-collapse-btn {
  padding: 0 12px;
  background: none;
  border: none;
  color: #B0AAA0;
  cursor: pointer;
  transition: color 0.15s;
  border-left: 1px solid #EFECE7;
}
.panel-collapse-btn:hover {
  color: #5A7D9A;
}
.unread-dot {
  display: inline-block;
  min-width: 16px;
  height: 16px;
  line-height: 16px;
  padding: 0 4px;
  margin-left: 5px;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  background: #D97B6B;
  border-radius: 8px;
  text-align: center;
  vertical-align: 1px;
}
/* ===== 分享弹窗 ===== */
.share-overlay {
  position: fixed;
  inset: 0;
  background: rgba(44, 44, 44, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1001;
  backdrop-filter: blur(4px);
}
.share-card {
  background: #FAFAF8;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 8px 40px rgba(44, 44, 44, 0.12);
}
.share-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 28px 32px 0;
  margin-bottom: 20px;
}
.share-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  letter-spacing: -0.2px;
  color: #2C2C2C;
}
.share-close {
  background: none;
  border: none;
  color: #A8A8A8;
  cursor: pointer;
  padding: 4px;
  display: flex;
  transition: color 0.15s;
}
.share-close:hover {
  color: #2C2C2C;
}
.share-body {
  padding: 0 32px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.share-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.share-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #A8A8A8;
  text-transform: uppercase;
}
.share-value {
  font-size: 14px;
  color: #2C2C2C;
  font-weight: 500;
}
.share-value--code {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 3px;
  color: #5A7D9A;
}
.share-value--link {
  font-size: 12px;
  color: #6B6B6B;
  word-break: break-all;
}
.share-copy-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.share-copy-btn {
  flex-shrink: 0;
  padding: 5px 14px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #5A7D9A;
  background: #EDF2F7;
  border: 1px solid #D0D9E4;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.share-copy-btn:hover {
  background: #5A7D9A;
  color: #FAFAF8;
  border-color: #5A7D9A;
}
.share-hint {
  font-size: 11px;
  color: #A8A8A8;
  text-align: center;
  padding-top: 8px;
  border-top: 1px solid #EFECE7;
}
.share-btn-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.share-icon-btn {
  padding: 5px 9px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.share-all-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 10px 0;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #FAFAF8;
  background: #5A7D9A;
  border: 1px solid #5A7D9A;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.share-all-btn:hover {
  background: #4C6D88;
  border-color: #4C6D88;
}

/* ===== 入会前设备预览 ===== */
.preview-overlay {
  position: fixed;
  inset: 0;
  background: rgba(44, 44, 44, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1002;
  backdrop-filter: blur(6px);
  padding: 24px;
}
.preview-card {
  background: #FAFAF8;
  width: 560px;
  max-width: 94vw;
  box-shadow: 0 8px 40px rgba(44, 44, 44, 0.18);
}
.preview-header {
  padding: 24px 28px 0;
}
.preview-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0;
  letter-spacing: -0.2px;
  color: #2C2C2C;
}
.preview-sub {
  font-size: 12px;
  color: #8A8A8A;
  margin: 6px 0 0;
}
.preview-video-wrap {
  margin: 20px 28px 0;
  aspect-ratio: 16 / 9;
  background: #2C2C2C;
  border-radius: 6px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.preview-video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: scaleX(-1);
}
.preview-video-placeholder {
  color: #A8A8A8;
  font-size: 13px;
}
.preview-controls {
  display: flex;
  gap: 16px;
  padding: 20px 28px 0;
}
.preview-field {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preview-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #A8A8A8;
  text-transform: uppercase;
}
.preview-select {
  height: 38px;
  padding: 0 10px;
  background: #EDF2F7;
  border: 1px solid #D0D9E4;
  border-radius: 4px;
  color: #2C2C2C;
  font-size: 13px;
  font-family: inherit;
  outline: none;
  cursor: pointer;
}
.preview-select:focus {
  border-color: #5A7D9A;
}
.preview-meter-wrap {
  padding: 16px 28px 0;
}
.preview-meter {
  margin-top: 8px;
  height: 6px;
  background: #EDF2F7;
  border-radius: 3px;
  overflow: hidden;
}
.preview-meter-fill {
  height: 100%;
  background: #5A7D9A;
  border-radius: 3px;
  transition: width 0.12s;
}
.preview-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 24px 28px 28px;
}
.preview-btn {
  padding: 9px 22px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #5A7D9A;
  background: transparent;
  border: 1px solid #D0D9E4;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.preview-btn:hover {
  border-color: #5A7D9A;
}
.preview-btn--primary {
  background: #5A7D9A;
  border-color: #5A7D9A;
  color: #FAFAF8;
}
.preview-btn--primary:hover {
  background: #4C6D88;
  border-color: #4C6D88;
}

/* ===== 公告弹窗 ===== */
.announce-input {
  width: 100%;
  padding: 12px 14px;
  background: #EDF2F7;
  border: 1px solid #D0D9E4;
  border-radius: 4px;
  color: #2C2C2C;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  box-sizing: border-box;
  transition: border-color 0.15s;
}
.announce-input:focus {
  border-color: #5A7D9A;
}
.announce-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.announce-btn {
  padding: 8px 20px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.3px;
  color: #5A7D9A;
  background: transparent;
  border: 1px solid #D0D9E4;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.announce-btn:hover {
  border-color: #5A7D9A;
}
.announce-btn--primary {
  background: #5A7D9A;
  border-color: #5A7D9A;
  color: #FAFAF8;
}
.announce-btn--primary:hover {
  background: #4C6D88;
  border-color: #4C6D88;
}

/* ===== 聊天标签 ===== */
.chat-tabs {
  display: flex;
  border-bottom: 1px solid #E8E4DE;
}
.chat-tab {
  flex: 1;
  padding: 10px 0;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.3px;
  border: none;
  background: none;
  color: #A8A8A8;
  cursor: pointer;
  font-family: inherit;
  position: relative;
  transition: color 0.15s;
}
.chat-tab:hover {
  color: #6B6B6B;
}
.chat-tab.active {
  color: #2C2C2C;
}
.chat-tab.active::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 50%;
  transform: translateX(-50%);
  width: 32px;
  height: 2px;
  background: #5A7D9A;
}

/* ===== 私聊选择器 ===== */
.whisper-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #EFECE7;
  background: #F7F5F2;
}
.whisper-label {
  font-size: 11px;
  color: #8C8C8C;
  font-weight: 500;
  white-space: nowrap;
}
.whisper-select {
  flex: 1;
  padding: 4px 8px;
  font-size: 12px;
  border: 1px solid #D5D2CC;
  background: #FAFAF8;
  color: #2C2C2C;
  font-family: inherit;
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s;
  max-width: 100%;
}
.whisper-select:focus {
  border-color: #5A7D9A;
}

/* ===== 聊天区 panel-header 保留兼容 ===== */
.panel-header {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid #e8e4dc;
  display: flex;
  align-items: center;
  gap: 6px;
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 系统消息 */
.msg-system {
  align-self: center;
  font-size: 11px;
  color: #aaa;
  padding: 2px 10px;
  margin: 4px 0;
}

/* 消息行 */
.msg-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  animation: msgIn 0.3s ease;
}
.msg-row.sent {
  flex-direction: row-reverse;
}
.msg-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
  margin-top: 2px;
}
.msg-avatar-img {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  margin-top: 2px;
}
.msg-body {
  max-width: 75%;
}
.msg-name {
  font-size: 10px;
  color: #999;
  margin-bottom: 2px;
  display: block;
}
.msg-bubble {
  padding: 7px 12px;
  border-radius: 14px;
  font-size: 13px;
  line-height: 1.5;
  background: #f0ede8;
  color: #3a3530;
  word-break: break-word;
}
.msg-bubble.mine {
  background: #d4e0d8;
}
.msg-bubble.whisper {
  background: #F5F0E8;
  border: 1px dashed #D5C8A8;
}
.msg-bubble.mine.whisper {
  background: #F5F0E8;
  border: 1px dashed #D5C8A8;
}
.msg-bubble.recalled {
  background: #F0EEE9;
  color: #A8A29A;
  font-style: italic;
}
.msg-recalled {
  font-size: 12px;
  color: #A8A29A;
}
.msg-meta-line {
  margin-top: 2px;
}
.msg-recall-btn {
  background: none;
  border: none;
  font-size: 10px;
  color: #C4BEB6;
  cursor: pointer;
  padding: 0;
  margin-right: 8px;
  transition: color 0.15s;
  font-family: inherit;
}
.msg-recall-btn:hover {
  color: #5A7D9A;
}
.msg-time {
  display: inline-block;
  font-size: 10px;
  color: #C4BEB6;
}
.msg-mention {
  color: #5A7D9A;
  font-weight: 600;
  background: #EDF2F7;
  border-radius: 3px;
  padding: 0 2px;
}
.whisper-tag {
  display: inline-block;
  font-size: 9px;
  font-weight: 600;
  color: #B8935A;
  background: #F5F0E8;
  padding: 1px 5px;
  margin-left: 4px;
  vertical-align: 1px;
  letter-spacing: 0.2px;
}
.msg-row.sent .msg-meta-line {
  text-align: right;
}

@keyframes msgIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 输入区 */
.chat-input-area {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid #e8e4dc;
  position: relative;
}
.mention-pop {
  position: absolute;
  bottom: 100%;
  left: 12px;
  right: 12px;
  margin-bottom: 6px;
  max-height: 180px;
  overflow-y: auto;
  background: #FAFAF8;
  border: 1px solid #D5D2CC;
  box-shadow: 0 6px 20px rgba(44, 44, 44, 0.12);
  z-index: 20;
  padding: 4px 0;
}
.mention-hint {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #A8A8A8;
  text-transform: uppercase;
  padding: 6px 14px 4px;
}
.mention-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 7px 14px;
  background: none;
  border: none;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  color: #2C2C2C;
  text-align: left;
  transition: background 0.12s;
}
.mention-item:hover {
  background: #EDF2F7;
}
.mention-name {
  font-weight: 500;
}
.mention-all {
  font-size: 10px;
  font-weight: 600;
  color: #5A7D9A;
  background: #EDF2F7;
  padding: 1px 6px;
}
.chat-input-area textarea {
  flex: 1;
  border: 1px solid #e8e4dc;
  border-radius: 8px;
  outline: none;
  padding: 7px 10px;
  font-size: 13px;
  font-family: inherit;
  resize: none;
  background: #faf8f5;
  color: #3a3530;
  line-height: 1.5;
}
.chat-input-area textarea::placeholder {
  color: #c4beb6;
}
.send-btn {
  width: 36px;
  height: 36px;
  border: 1px solid #d4cfc6;
  border-radius: 50%;
  background: #fff;
  color: #555;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
  align-self: flex-end;
}
.send-btn:hover:not(:disabled) {
  background: #3a3530;
  color: #fff;
}
.send-btn:disabled {
  opacity: 0.3;
  cursor: default;
}

/* 参会者 */
.participants-area {
  border-top: 1px solid #e8e4dc;
  max-height: 200px;
  overflow-y: auto;
}
.person-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-size: 12px;
  border-bottom: 1px solid #f5f3ef;
}
.person-row:hover {
  background: #faf8f5;
}
.person-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.person-status {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 2px solid #e0afa0;
  background: transparent;
}
.person-status.on {
  background: #7a9e7e;
  border-color: #7a9e7e;
}

/* 底部控制栏 */
.control-bar {
  display: flex;
  justify-content: center;
  gap: 12px;
  padding: 12px 20px;
  background: #fff;
  border-top: 1px solid #e8e4dc;
  flex-shrink: 0;
}
.ctrl-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border: 1px solid #d4cfc6;
  border-radius: 8px;
  background: #fff;
  color: #555;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.ctrl-btn:hover {
  background: #f0ede8;
}
.ctrl-btn.on {
  border-color: #5b8c9e;
  color: #5b8c9e;
  background: #eef4f7;
}
.ctrl-btn.off {
  border-color: #e0afa0;
  color: #c47a6b;
  background: #fdf0ed;
}
.ctrl-warn {
  border-color: #d4a853;
  color: #8b6914;
}
.ctrl-warn:hover {
  background: #fdf8e8;
}
.ctrl-danger {
  border-color: #e0afa0;
  color: #c47a6b;
}
.ctrl-danger:hover {
  background: #fdf0ed;
}

/* ===== 等候室 ===== */
.waiting-overlay {
  position: fixed;
  inset: 0;
  background: #2C2C2C;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.waiting-card {
  text-align: center;
  color: #FAFAF8;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}
.waiting-icon {
  color: #5A7D9A;
  margin-bottom: 8px;
}
.waiting-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
  letter-spacing: -0.3px;
}
.waiting-desc {
  font-size: 14px;
  color: #A8A8A8;
  margin: 0;
}
.waiting-dots {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.waiting-dots .dot {
  width: 6px;
  height: 6px;
  background: #5A7D9A;
  border-radius: 50%;
  animation: dotPulse 1.4s ease-in-out infinite;
}
.waiting-dots .dot:nth-child(2) { animation-delay: 0.2s; }
.waiting-dots .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes dotPulse {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}
.waiting-btn {
  margin-top: 16px;
  padding: 8px 28px;
  background: transparent;
  border: 1px solid #6B6B6B;
  color: #A8A8A8;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.waiting-btn:hover {
  border-color: #C0392B;
  color: #C0392B;
}

.waiting-btn--ghost {
  border-color: transparent;
  color: #6B6B6B;
  margin-top: 4px;
}
.waiting-btn--ghost:hover {
  border-color: transparent;
  color: #A8A8A8;
}

.password-row {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}

.password-input {
  width: 180px;
  height: 40px;
  padding: 0 14px;
  background: #3A3A3A;
  border: 1px solid #555;
  border-radius: 6px;
  color: #FAFAF8;
  font-size: 16px;
  font-family: inherit;
  text-align: center;
  letter-spacing: 4px;
  outline: none;
  transition: border-color 0.2s;
}

.password-input:focus {
  border-color: #5A7D9A;
}

.password-error {
  color: #C07060;
  font-size: 13px;
  margin: 0;
}

/* 等候室管理面板 */
.waiting-room-panel {
  border-top: 1px solid #E8E4DE;
  margin-top: 12px;
  padding-top: 12px;
}
.waiting-room-panel .panel-header {
  color: #5A7D9A;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  padding: 4px 0 8px;
  margin-bottom: 4px;
  border-bottom: 1px dashed #E8E4DE;
}
.btn-accept {
  background: #5A7D9A;
  color: #fff;
  border: none;
  padding: 2px 10px;
  font-size: 11px;
  border-radius: 2px;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-accept:hover {
  background: #4a6d8a;
}

/* ===== 表情反应 ===== */
.reaction-bar {
  display: flex;
  gap: 4px;
  padding: 8px 12px;
  border-top: 1px solid #E8E4DE;
}
.reaction-btn {
  background: none;
  border: 1px solid transparent;
  font-size: 18px;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 4px;
  transition: background 0.15s, transform 0.15s;
  line-height: 1;
}
.reaction-btn:hover {
  background: #F2F0EB;
  transform: scale(1.3);
}

/* 浮动表情动画 */
.floating-reactions {
  position: fixed;
  bottom: 120px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 200;
  display: flex;
  gap: 8px;
  pointer-events: none;
}
.float-emoji {
  font-size: 32px;
  animation: floatUp 2s ease-out forwards;
  opacity: 0;
}
@keyframes floatUp {
  0% { opacity: 1; transform: translateY(0) scale(0.5); }
  20% { opacity: 1; transform: translateY(-20px) scale(1.2); }
  100% { opacity: 0; transform: translateY(-120px) scale(1); }
}

::-webkit-scrollbar {
  width: 4px;
}
::-webkit-scrollbar-thumb {
  background: #d4cfc6;
  border-radius: 2px;
}

/* ===== 防截屏水印 ===== */
.watermark-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 999;
  overflow: hidden;
}
.watermark-pattern {
  display: flex;
  flex-wrap: wrap;
  gap: 60px 80px;
  transform: rotate(-20deg) scale(1.5);
  transform-origin: center;
  padding: 40px;
  opacity: 0.06;
}
.watermark-text {
  font-size: 13px;
  color: #2C2C2C;
  white-space: nowrap;
  font-weight: 600;
  letter-spacing: 1px;
}

/* ===== 虚拟背景 ===== */
.bg-canvas {
  display: none;
}
.rec-canvas {
  position: absolute;
  left: -9999px;
  top: 0;
}
.bg-picker-wrap {
  position: relative;
}
.bg-picker {
  position: absolute;
  bottom: 56px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 8px;
  background: #FAFAF8;
  border: 1px solid #E0DCD5;
  border-radius: 10px;
  padding: 10px 14px;
  z-index: 100;
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
.bg-option {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 2px solid #E0DCD5;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #2C2C2C;
  background: #FAFAF8;
  cursor: pointer;
  transition: border-color 0.2s;
}
.bg-option:hover {
  border-color: #5A7D9A;
}
.bg-option.active {
  border-color: #5A7D9A;
  border-width: 3px;
}
.bg-upload {
  cursor: pointer;
}
.bg-upload:hover {
  border-color: #5A7D9A;
  background: #EEF4F7;
}
</style>