<template>
  <div class="home-container">
    <div class="header">
      <h1>局域网视频会议</h1>
      <div class="user-info">
        <span>{{ displayName }}</span>
        <el-button type="danger" size="small" @click="handleLogout">退出</el-button>
      </div>
    </div>

    <div class="main">
      <div class="card create-card" @click="showCreateDialog = true">
        <el-icon :size="48"><Plus /></el-icon>
        <h2>创建会议</h2>
        <p>创建一个新会议并邀请成员</p>
      </div>

      <div class="card join-card" @click="showJoinDialog = true">
        <el-icon :size="48"><Link /></el-icon>
        <h2>加入会议</h2>
        <p>输入会议号加入已有会议</p>
      </div>
    </div>

    <!-- 创建会议对话框 -->
    <el-dialog v-model="showCreateDialog" title="创建会议" width="400px">
      <el-form :model="createForm">
        <el-form-item label="会议标题">
          <el-input v-model="createForm.title" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreate" :loading="creating">创建</el-button>
      </template>
    </el-dialog>

    <!-- 加入会议对话框 -->
    <el-dialog v-model="showJoinDialog" title="加入会议" width="400px">
      <el-form :model="joinForm">
        <el-form-item label="会议号">
          <el-input v-model="joinForm.meetingNo" placeholder="输入6位会议号" maxlength="6" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showJoinDialog = false">取消</el-button>
        <el-button type="primary" @click="handleJoin" :loading="joining">加入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const API_BASE = 'http://localhost:8000/api/v1'
const router = useRouter()

const displayName = localStorage.getItem('displayName') || ''

const showCreateDialog = ref(false)
const showJoinDialog = ref(false)
const creating = ref(false)
const joining = ref(false)

const createForm = ref({ title: '' })
const joinForm = ref({ meetingNo: '' })

const handleLogout = () => {
  localStorage.clear()
  router.push('/login')
}

const handleCreate = async () => {
  creating.value = true
  try {
    const res = await axios.post(`${API_BASE}/meetings`, {
      title: createForm.value.title,
      creator_name: displayName
    }, {
      headers: { Authorization: `Bearer ${localStorage.getItem('token')}` }
    })
    ElMessage.success(`会议已创建，会议号: ${res.data.meeting_no}`)
    showCreateDialog.value = false
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

const handleJoin = async () => {
  if (joinForm.value.meetingNo.length !== 6) {
    ElMessage.warning('请输入6位会议号')
    return
  }
  joining.value = true
  try {
    await axios.post(`${API_BASE}/meetings/${joinForm.value.meetingNo}/join`, {
      display_name: displayName
    }, {
      headers: { Authorization: `Bearer ${localStorage.getItem('token')}` }
    })
    ElMessage.success('加入成功')
    router.push(`/meeting/${joinForm.value.meetingNo}`)
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || '加入失败')
  } finally {
    joining.value = false
  }
}
</script>

<style scoped>
.home-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #1A535C 0%, #4ECDC4 100%);
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 40px;
  color: white;
}

.header h1 { margin: 0; font-size: 24px; }
.user-info { display: flex; align-items: center; gap: 16px; }

.main {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 40px;
  min-height: 70vh;
  padding: 20px;
}

.card {
  width: 280px;
  height: 220px;
  background: white;
  border-radius: 16px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  cursor: pointer;
  transition: all 0.3s;
  box-shadow: 0 8px 24px rgba(0,0,0,0.1);
}

.card:hover {
  transform: translateY(-8px);
  box-shadow: 0 12px 32px rgba(0,0,0,0.2);
}

.card h2 { margin: 16px 0 8px; color: #333; }
.card p { margin: 0; color: #999; font-size: 14px; }
</style>