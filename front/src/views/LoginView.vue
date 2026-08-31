<template>
	<div class="login-container">
		<!-- 背景特效 -->
		<div class="background-effects">
			<div class="effect-circle pink"></div>
			<div class="effect-circle light-pink"></div>
			<div class="effect-circle purple"></div>
			<div class="effect-bubble"></div>
			<div class="effect-bubble"></div>
			<div class="effect-bubble"></div>
		</div>

		<div class="login-box">
			<!-- 标题和切换按钮 -->
			<div class="header">
				<h1 class="title">视频会议平台</h1>
				<div class="mode-switch">
					<el-button :class="activeTab === 'login' ? 'active-tab' : 'inactive-tab'"
						@click="activeTab = 'login'" class="mode-button">
						登录
					</el-button>
					<el-button :class="activeTab === 'register' ? 'active-tab' : 'inactive-tab'"
						@click="activeTab = 'register'" class="mode-button">
						注册
					</el-button>
				</div>
			</div>

			<!-- 登录表单 -->
			<el-form v-show="activeTab === 'login'" :model="loginForm" :rules="loginRules" ref="loginFormRef"
				class="form" @keyup.enter="handleLogin">
				<el-form-item prop="username">
					<el-input v-model="loginForm.username" placeholder="用户名" prefix-icon="User" size="large" />
				</el-form-item>

				<el-form-item prop="password">
					<el-input v-model="loginForm.password" type="password" placeholder="密码" prefix-icon="Lock"
						size="large" show-password />
				</el-form-item>

				<div class="forgot-password" @click="showForgotPassword = true">
					<el-icon>
						<QuestionFilled />
					</el-icon>
					<span>忘记密码?</span>
				</div>

				<el-button type="primary" size="large" @click="handleLogin" class="submit-button" :loading="loading">
					登录
				</el-button>
			</el-form>

			<!-- 注册表单 -->
			<el-form v-show="activeTab === 'register'" :model="registerForm" :rules="registerRules"
				ref="registerFormRef" class="form" @keyup.enter="handleRegister">
				<el-form-item prop="username">
					<el-input v-model="registerForm.username" placeholder="用户名" prefix-icon="User" size="large" />
				</el-form-item>

				<el-form-item prop="display_name">
					<el-input v-model="registerForm.display_name" placeholder="显示昵称" prefix-icon="User" size="large" />
				</el-form-item>

				<el-form-item prop="email">
					<el-input v-model="registerForm.email" placeholder="邮箱" prefix-icon="Message" size="large" />
				</el-form-item>

				<el-form-item prop="phone">
					<el-input v-model="registerForm.phone" placeholder="手机号 (可选)" prefix-icon="Phone" size="large" />
				</el-form-item>

				<el-form-item prop="password">
					<el-input v-model="registerForm.password" type="password" placeholder="密码" prefix-icon="Lock"
						size="large" show-password />
				</el-form-item>

				<el-form-item prop="confirmPassword">
					<el-input v-model="registerForm.confirmPassword" type="password" placeholder="确认密码"
						prefix-icon="Lock" size="large" show-password />
				</el-form-item>

				<el-button type="primary" size="large" @click="handleRegister" class="submit-button" :loading="loading">
					注册
				</el-button>
			</el-form>
		</div>

		<!-- 忘记密码对话框 -->
		<el-dialog v-model="showForgotPassword" title="找回密码" width="400px" class="forgot-password-dialog">
			<div class="forgot-password-content">
				<el-steps :active="forgotStep" align-center>
					<el-step title="验证身份" />
					<el-step title="重置密码" />
					<el-step title="完成" />
				</el-steps>

				<div v-if="forgotStep === 1" class="step-content">
					<p>请输入您的注册邮箱，我们将发送验证码</p>
					<el-input v-model="forgotEmail" placeholder="请输入邮箱" prefix-icon="Message" class="step-input" />
				</div>

				<div v-if="forgotStep === 2" class="step-content">
					<p>请输入收到的验证码和新密码</p>
					<el-input v-model="forgotCode" placeholder="验证码" prefix-icon="Key" class="step-input" />
					<el-input v-model="forgotNewPassword" type="password" placeholder="新密码" prefix-icon="Lock"
						class="step-input" show-password />
				</div>

				<div v-if="forgotStep === 3" class="step-success">
					<el-icon color="#67C23A" size="60px">
						<CircleCheckFilled />
					</el-icon>
					<p>密码重置成功！</p>
				</div>

				<div class="step-actions">
					<el-button v-if="forgotStep > 1 && forgotStep < 3" @click="forgotStep--" class="step-button">
						上一步
					</el-button>
					<el-button v-if="forgotStep < 3" type="primary" @click="handleForgotStep" class="step-button" :loading="forgotLoading">
						{{ forgotStep === 1 ? '发送验证码' : '重置密码' }}
					</el-button>
					<el-button v-if="forgotStep === 3" type="primary" @click="showForgotPassword = false"
						class="step-button">
						完成
					</el-button>
				</div>
			</div>
		</el-dialog>
	</div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useRouter } from 'vue-router'
import axios from 'axios'

const API_BASE = '/api/v1'
const router = useRouter()

// 登录注册切换
const activeTab = ref<'login' | 'register'>('login')

// 登录表单
const loginForm = ref({
  username: '',
  password: ''
})

const loginFormRef = ref<FormInstance>()
const loginRules = ref<FormRules>({
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '长度在 3 到 32 个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 20, message: '长度在 6 到 20 个字符', trigger: 'blur' }
  ]
})

// 注册表单（加了 display_name 字段）
const registerForm = ref({
  username: '',
  display_name: '',
  email: '',
  phone: '',
  password: '',
  confirmPassword: ''
})

const registerFormRef = ref<FormInstance>()
const registerRules = ref<FormRules>({
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '长度在 3 到 32 个字符', trigger: 'blur' }
  ],
  display_name: [
    { required: true, message: '请输入显示昵称', trigger: 'blur' },
    { min: 1, max: 64, message: '长度在 1 到 64 个字符', trigger: 'blur' }
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入正确的邮箱地址', trigger: ['blur', 'change'] }
  ],
  phone: [
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的手机号', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 20, message: '长度在 6 到 20 个字符', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validatePassword, trigger: 'blur' }
  ]
})

function validatePassword(rule: any, value: string, callback: any) {
  if (value !== registerForm.value.password) {
    callback(new Error('两次输入密码不一致!'))
  } else {
    callback()
  }
}

// 忘记密码
const showForgotPassword = ref(false)
const forgotStep = ref(1)
const forgotEmail = ref('')
const forgotCode = ref('')
const forgotNewPassword = ref('')

// 加载状态
const loading = ref(false)
const forgotLoading = ref(false)

// 登录方法
const handleLogin = async () => {
  try {
    await loginFormRef.value?.validate()
    loading.value = true
    
    const res = await axios.post(`${API_BASE}/auth/login`, loginForm.value)
    const d = res.data.data
    
    localStorage.setItem('token', d.access_token)
    localStorage.setItem('userId', String(d.user_id))
    localStorage.setItem('username', d.username)
    localStorage.setItem('displayName', d.display_name)
    
    ElMessage.success(res.data.message || '登录成功')
    router.push('/')
  } catch (error: any) {
    console.error('登录失败:', error)
    ElMessage.error(error.response?.data?.message || '登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}

// 注册方法
const handleRegister = async () => {
  try {
    await registerFormRef.value?.validate()
    loading.value = true
    
    const res = await axios.post(`${API_BASE}/auth/register`, {
      username: registerForm.value.username,
      display_name: registerForm.value.display_name,
      email: registerForm.value.email,
      phone: registerForm.value.phone || undefined,
      password: registerForm.value.password
    })
    const d = res.data.data
    
    localStorage.setItem('token', d.access_token)
    localStorage.setItem('userId', String(d.user_id))
    localStorage.setItem('username', d.username)
    localStorage.setItem('displayName', d.display_name)
    
    ElMessage.success(res.data.message || '注册成功')
    router.push('/')
  } catch (error: any) {
    console.error('注册失败:', error)
    ElMessage.error(error.response?.data?.message || '注册失败，请检查输入信息')
  } finally {
    loading.value = false
  }
}

// 忘记密码步骤
const handleForgotStep = async () => {
  if (forgotStep.value === 1) {
    if (!forgotEmail.value) {
      ElMessage.warning('请输入邮箱')
      return
    }
    forgotLoading.value = true
    try {
      await axios.post(`${API_BASE}/auth/send-code`, { email: forgotEmail.value })
      ElMessage.success('验证码已发送')
      forgotStep.value = 2
    } catch (error: any) {
      ElMessage.error(error.response?.data?.message || '发送失败')
    } finally {
      forgotLoading.value = false
    }
  } else if (forgotStep.value === 2) {
    if (!forgotCode.value || !forgotNewPassword.value) {
      ElMessage.warning('请填写完整')
      return
    }
    forgotLoading.value = true
    try {
      await axios.post(`${API_BASE}/auth/reset-password`, {
        email: forgotEmail.value,
        code: forgotCode.value,
        new_password: forgotNewPassword.value
      })
      ElMessage.success('密码重置成功')
      forgotStep.value = 3
    } catch (error: any) {
      ElMessage.error(error.response?.data?.message || '重置失败')
    } finally {
      forgotLoading.value = false
    }
  }
}

onMounted(() => {
  const token = localStorage.getItem('token')
  if (token) {
    router.push('/')
  }
})
</script>

<style scoped lang="scss">
	.login-container {
		display: flex;
		justify-content: center;
		align-items: center;
		min-height: 100vh;
		background-image: url(../assets/联想截图_20260222163313.png);
		background-size: cover;
		background-position: center;
		background-repeat: no-repeat;
		position: relative;
		overflow: hidden;
	}

	.background-effects {
		position: absolute;
		width: 100%;
		height: 100%;
		top: 0;
		left: 0;
		z-index: 0;

		.effect-circle {
			position: absolute;
			border-radius: 50%;
			filter: blur(60px);
			opacity: 0.6;

			&.pink {
				width: 300px;
				height: 300px;
				background:#FFF9C2;
				top: -100px;
				left: -100px;
			}

			&.light-pink {
				width: 400px;
				height: 400px;
				background: #FFF3D4;
				bottom: -150px;
				right: -100px;
			}

			&.purple {
				width: 250px;
				height: 250px;
				background: #D8BFD8;
				top: 50%;
				left: 70%;
			}
		}

		.effect-bubble {
			position: absolute;
			border-radius: 50%;
			background: rgba(255, 255, 255, 0.3);

			&:nth-child(4) {
				width: 30px;
				height: 30px;
				top: 20%;
				left: 10%;
				animation: float 8s infinite ease-in-out;
			}

			&:nth-child(5) {
				width: 50px;
				height: 50px;
				top: 60%;
				left: 80%;
				animation: float 10s infinite ease-in-out 2s;
			}

			&:nth-child(6) {
				width: 20px;
				height: 20px;
				top: 80%;
				left: 30%;
				animation: float 6s infinite ease-in-out 1s;
			}
		}
	}

	@keyframes float {

		0%,
		100% {
			transform: translateY(0) translateX(0);
		}

		50% {
			transform: translateY(-50px) translateX(20px);
		}
	}

	.login-box {
		position: relative;
		width: 420px;
		padding: 40px;
		backdrop-filter: blur(5px);
		border-radius: 20px;
		box-shadow: 0 10px 30px rgba(255, 105, 180, 0.2);
		z-index: 1;
		backdrop-filter: blur(5px);
		border: 1px solid rgba(255, 255, 255, 0.3);
		overflow: hidden;

		&::before {
			content: '';
			position: absolute;
			top: 0;
			left: 0;
			right: 0;
			height: 5px;
		}
	}

	.header {
		text-align: center;
		margin-bottom: 30px;

		.title {
			color: #4ECDC4;
			font-size: 24px;
			margin-bottom: 20px;
			font-weight: bold;
			text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.1);
		}
	}

	.mode-switch {
		display: flex;
		justify-content: center;
		margin-bottom: 20px;

		.mode-button {
			border: none !important;
			font-weight: bold;
			transition: all 0.3s;

			&:hover {
				transform: translateY(-2px);
			}
		}

		.active-tab {
			background-color: #4ECDC4 !important;
			color: white !important;
			border-radius: 8px;
			box-shadow: 0 2px 8px rgba(255, 105, 180, 0.3);

			&:hover {
				background-color: #F284B6!important;
				color: white !important;
				opacity: 0.9;
			}
		}

		.inactive-tab {
			background-color: transparent !important;
			color: #FFE66D !important;

			&:hover {
				background-color: rgba(255, 105, 180, 0.1) !important;
				color: #FFE66D !important;
			}
		}
	}

	.form {
		margin-top: 20px;

		:deep(.el-input__wrapper) {
			border-radius: 10px;
			box-shadow: 0 2px 8px rgba(255, 105, 180, 0.1);

			&:hover {
				box-shadow: 0 2px 8px rgba(255, 105, 180, 0.2);
			}
		}

		:deep(.el-input__inner) {
			color: #FF69B4;
		}

		:deep(.el-icon) {
			color: #FF69B4;
		}
	}

	.forgot-password {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		color: #4ECDC4;
		font-size: 14px;
		margin: -10px 0 20px;
		cursor: pointer;
		transition: all 0.3s;

		&:hover {
			color: #D8BFD8;
			text-decoration: underline;
		}

		.el-icon {
			margin-right: 5px;
		}
	}

	.submit-button {
		width: 100%;
		margin-top: 10px;
		background: linear-gradient(90deg, #1A535C 0%, #4ECDC4 100%);
		border: none;
		border-radius: 10px;
		font-weight: bold;
		letter-spacing: 1px;
		height: 45px;
		transition: all 0.3s;

		&:hover {
			transform: translateY(-2px);
			box-shadow: 0 5px 15px rgba(255, 105, 180, 0.3);
		}
	}

	@keyframes float-character {

		0%,
		100% {
			transform: translateY(0);
		}

		50% {
			transform: translateY(-10px);
		}
	}

	.forgot-password-dialog {
		:deep(.el-dialog) {
			border-radius: 15px;
			overflow: hidden;

			&::before {
				content: '';
				position: absolute;
				top: 0;
				left: 0;
				right: 0;
				height: 5px;
				background: linear-gradient(90deg, #FF69B4, #FFB6C1, #D8BFD8);
			}
		}

		:deep(.el-dialog__header) {
			border-bottom: 1px solid #FFE4E1;
			margin-right: 0;
		}

		:deep(.el-dialog__title) {
			color: #FF69B4;
			font-weight: bold;
		}
	}

	.forgot-password-content {
		padding: 0 20px;

		.step-content {
			margin: 30px 0;
			text-align: center;

			p {
				color: #666;
				margin-bottom: 20px;
			}
		}

		.step-input {
			margin-bottom: 15px;
		}

		.step-success {
			text-align: center;
			padding: 30px 0;

			p {
				margin-top: 15px;
				color: #FF69B4;
				font-weight: bold;
			}
		}

		.step-actions {
			display: flex;
			justify-content: center;
			margin-top: 30px;
		}
		
		.step-button {
			margin: 0 10px;
			border-radius: 8px;
		}
	}

	:deep(.el-step__head.is-process) {
		color: #FF69B4;
		border-color: #FF69B4;
	}

	:deep(.el-step__title.is-process) {
		color: #FF69B4;
		font-weight: bold;
	}
	
	.el-icon svg{
		color: #4ECDC4;
	}
</style>