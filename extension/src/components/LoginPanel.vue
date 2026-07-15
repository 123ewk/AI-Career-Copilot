<script setup lang="ts">
/**
 * 登录/注册面板组件
 *
 * 职责：
 * - 支持「登录」与「注册」两种模式切换
 * - 登录：输入邮箱、密码、后端 base URL，调用 POST /api/auth/login
 * - 注册：额外输入确认密码、昵称，调用 POST /api/auth/register
 * - 注册成功后自动同步登录态到 Service Worker，并触发 logged-in 事件
 * - access_token 仅存 Service Worker 内存，不写入 localStorage
 * - refresh_token 由后端 HttpOnly Cookie 自动管理
 *
 * 数据流：
 *   LoginPanel → POST /api/auth/login|register
 *              → 拿到 { access_token, user }
 *              → sendMessageToBackground(AUTH_TOKEN_UPDATED, { token, backendUrl, user })
 *              → Service Worker 内存存储 token
 *              → emit('logged-in', user) 通知父组件
 */
import { computed, reactive, ref } from 'vue'
import { z } from 'zod'
import {
  ChromeMessageType,
  sendMessageToBackground,
} from '../messaging/chrome_message'

/** 表单数据 */
interface AuthForm {
  email: string
  password: string
  passwordConfirm: string
  name: string
  backendUrl: string
}

/** 认证响应（与后端 TokenResponse 对齐） */
interface AuthResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: {
    id: string
    email: string
    name: string
  }
}

/** 后端错误响应结构 */
interface BackendErrorBody {
  error_code?: string
  detail?: string
  request_id?: string
}

const props = defineProps<{
  /** 初始后端 URL（默认 localhost:8000） */
  defaultBackendUrl?: string
}>()

const emit = defineEmits<{
  /** 登录/注册成功事件 */
  (e: 'logged-in', payload: { user: AuthResponse['user']; backendUrl: string }): void
}>()

// ==================== 表单状态 ====================

const mode = ref<'login' | 'register'>('login')

const form = reactive<AuthForm>({
  email: '',
  password: '',
  passwordConfirm: '',
  name: '',
  backendUrl: props.defaultBackendUrl ?? 'http://localhost:8000',
})

const loading = ref(false)
const errorMsg = ref<string | null>(null)
const successMsg = ref<string | null>(null)
const showPassword = ref(false)
const showPasswordConfirm = ref(false)

// ==================== 密码强度校验 ====================

const weakPasswords = new Set([
  '12345678',
  '123456789',
  '1234567890',
  'password',
  'password1',
  'qwerty123',
  'abc123456',
  'iloveyou1',
  'admin1234',
  'letmein1',
  'welcome1',
  'monkey1',
  'dragon1',
  'master1',
  'sunshine1',
  'princess1',
  'football1',
  'baseball1',
  'superman1',
  'trustno1',
  'hello123',
  'charlie1',
  'donald1',
  'password!',
])

function getPasswordStrengthError(password: string): string | undefined {
  if (password.length < 8) return '密码长度不能少于 8 位'
  if (password.length > 64) return '密码长度不能超过 64 位'
  if (!/[a-zA-Z]/.test(password) || !/\d/.test(password)) {
    return '密码必须同时包含字母和数字'
  }
  if (weakPasswords.has(password.toLowerCase())) return '密码过于常见，请更换'
  if (/^[a-zA-Z]+$/.test(password) || /^\d+$/.test(password)) {
    return '密码不能为纯字母或纯数字'
  }
  return undefined
}

// ==================== 表单校验 ====================

const baseSchema = z.object({
  email: z.string().email('邮箱格式不正确'),
  password: z.string().min(1, '密码不能为空'),
  backendUrl: z
    .string()
    .url('后端地址格式不正确')
    .refine((url) => url.startsWith('http://') || url.startsWith('https://'), {
      message: '后端地址必须以 http:// 或 https:// 开头',
    }),
})

const validationErrors = ref<Partial<Record<keyof AuthForm, string>>>({})

const isFormValid = computed(() => {
  let issues: z.ZodIssue[] = []

  if (mode.value === 'register') {
    const registerSchema = baseSchema.extend({
      password: z.string().superRefine((val, ctx) => {
        const err = getPasswordStrengthError(val)
        if (err) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: err,
          })
        }
      }),
      passwordConfirm: z.string().min(1, '请确认密码'),
      name: z.string().min(1, '昵称不能为空').max(32, '昵称不能超过 32 位'),
    })
    const result = registerSchema.safeParse(form)
    if (result.success) {
      if (form.password !== form.passwordConfirm) {
        issues.push({
          path: ['passwordConfirm'],
          message: '两次输入的密码不一致',
        } as z.ZodIssue)
      }
    } else {
      issues = result.error.issues
    }
  } else {
    const result = baseSchema.safeParse(form)
    if (!result.success) {
      issues = result.error.issues
    }
  }

  if (issues.length === 0) {
    validationErrors.value = {}
    return true
  }

  const errors: Partial<Record<keyof AuthForm, string>> = {}
  for (const issue of issues) {
    const field = issue.path[0] as keyof AuthForm
    if (!errors[field]) errors[field] = issue.message
  }
  validationErrors.value = errors
  return false
})

// ==================== 认证逻辑 ====================

async function handleSubmit() {
  if (!isFormValid.value) {
    errorMsg.value = '请修正表单错误后重试'
    return
  }

  loading.value = true
  errorMsg.value = null
  successMsg.value = null

  const isRegister = mode.value === 'register'
  const endpoint = isRegister ? '/api/auth/register' : '/api/auth/login'
  const body = isRegister
    ? {
        email: form.email,
        password: form.password,
        password_confirm: form.passwordConfirm,
        name: form.name,
      }
    : {
        email: form.email,
        password: form.password,
      }

  try {
    const resp = await fetch(`${form.backendUrl}${endpoint}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Request-Id': `ext-${mode.value}-${Date.now()}`,
      },
      credentials: 'include',
      body: JSON.stringify(body),
    })

    if (!resp.ok) {
      let errBody: BackendErrorBody | undefined
      try {
        errBody = (await resp.json()) as BackendErrorBody
      } catch {
        // JSON 解析失败，使用通用错误
      }

      if (resp.status === 401) {
        errorMsg.value = '邮箱或密码错误'
      } else if (resp.status === 409) {
        errorMsg.value = '该邮箱已被注册'
      } else if (resp.status === 422) {
        errorMsg.value = errBody?.detail ?? '请求参数校验失败'
      } else if (resp.status >= 500) {
        errorMsg.value = '后端服务异常，请稍后重试'
      } else {
        errorMsg.value = errBody?.detail ?? `${isRegister ? '注册' : '登录'}失败 (${resp.status})`
      }
      return
    }

    const data: AuthResponse = await resp.json()

    const messageResp = await sendMessageToBackground(
      ChromeMessageType.AUTH_TOKEN_UPDATED,
      {
        accessToken: data.access_token,
        backendUrl: form.backendUrl,
        user: {
          id: data.user.id,
          email: data.user.email,
          name: data.user.name,
        },
        expiresIn: data.expires_in,
      },
    )

    if (!messageResp.ok) {
      errorMsg.value = `同步登录态失败：${messageResp.error ?? '未知错误'}`
      return
    }

    successMsg.value = `${isRegister ? '注册' : '登录'}成功，欢迎 ${data.user.name || data.user.email}`
    emit('logged-in', { user: data.user, backendUrl: form.backendUrl })

    form.password = ''
    form.passwordConfirm = ''
  } catch (err) {
    if (err instanceof TypeError && err.message.includes('fetch')) {
      errorMsg.value = `无法连接后端 ${form.backendUrl}，请确认服务已启动`
    } else {
      errorMsg.value = `${isRegister ? '注册' : '登录'}失败：${err instanceof Error ? err.message : String(err)}`
    }
  } finally {
    loading.value = false
  }
}

// ==================== 辅助方法 ====================

function togglePassword() {
  showPassword.value = !showPassword.value
}

function togglePasswordConfirm() {
  showPasswordConfirm.value = !showPasswordConfirm.value
}

function clearError(field: keyof AuthForm) {
  if (validationErrors.value[field]) {
    delete validationErrors.value[field]
  }
  errorMsg.value = null
}

function switchMode(newMode: 'login' | 'register') {
  mode.value = newMode
  errorMsg.value = null
  successMsg.value = null
  validationErrors.value = {}
}
</script>

<template>
  <div class="login-panel">
    <!-- 模式切换 -->
    <div class="mode-tabs">
      <button
        type="button"
        class="mode-tab"
        :class="{ active: mode === 'login' }"
        @click="switchMode('login')"
      >
        登录
      </button>
      <button
        type="button"
        class="mode-tab"
        :class="{ active: mode === 'register' }"
        @click="switchMode('register')"
      >
        注册
      </button>
    </div>

    <h2 class="login-title">
      {{ mode === 'login' ? '登录 AI Career Copilot' : '注册新账号' }}
    </h2>

    <!-- 错误提示 -->
    <div v-if="errorMsg" class="alert alert-error">
      <span>{{ errorMsg }}</span>
    </div>

    <!-- 成功提示 -->
    <div v-if="successMsg" class="alert alert-success">
      <span>{{ successMsg }}</span>
    </div>

    <!-- 表单 -->
    <form class="login-form" @submit.prevent="handleSubmit">
      <!-- 邮箱 -->
      <div class="form-field">
        <label for="email">邮箱</label>
        <input
          id="email"
          v-model="form.email"
          type="email"
          autocomplete="email"
          placeholder="you@example.com"
          :disabled="loading"
          :class="{ 'input-error': validationErrors.email }"
          @input="clearError('email')"
        />
        <p v-if="validationErrors.email" class="field-error">
          {{ validationErrors.email }}
        </p>
      </div>

      <!-- 昵称（仅注册） -->
      <div v-if="mode === 'register'" class="form-field">
        <label for="name">昵称</label>
        <input
          id="name"
          v-model="form.name"
          type="text"
          autocomplete="name"
          placeholder="你的名字"
          :disabled="loading"
          :class="{ 'input-error': validationErrors.name }"
          @input="clearError('name')"
        />
        <p v-if="validationErrors.name" class="field-error">
          {{ validationErrors.name }}
        </p>
      </div>

      <!-- 密码 -->
      <div class="form-field">
        <label for="password">密码</label>
        <div class="password-wrapper">
          <input
            id="password"
            v-model="form.password"
            :type="showPassword ? 'text' : 'password'"
            :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
            placeholder="请输入密码"
            :disabled="loading"
            :class="{ 'input-error': validationErrors.password }"
            @input="clearError('password')"
          />
          <button
            type="button"
            class="toggle-btn"
            :title="showPassword ? '隐藏密码' : '显示密码'"
            @click="togglePassword"
          >
            {{ showPassword ? '🙈' : '👁' }}
          </button>
        </div>
        <p v-if="validationErrors.password" class="field-error">
          {{ validationErrors.password }}
        </p>
        <p v-else-if="mode === 'register'" class="field-hint">
          长度 8-64 位，需同时包含字母和数字
        </p>
      </div>

      <!-- 确认密码（仅注册） -->
      <div v-if="mode === 'register'" class="form-field">
        <label for="passwordConfirm">确认密码</label>
        <div class="password-wrapper">
          <input
            id="passwordConfirm"
            v-model="form.passwordConfirm"
            :type="showPasswordConfirm ? 'text' : 'password'"
            autocomplete="new-password"
            placeholder="请再次输入密码"
            :disabled="loading"
            :class="{ 'input-error': validationErrors.passwordConfirm }"
            @input="clearError('passwordConfirm')"
          />
          <button
            type="button"
            class="toggle-btn"
            :title="showPasswordConfirm ? '隐藏密码' : '显示密码'"
            @click="togglePasswordConfirm"
          >
            {{ showPasswordConfirm ? '🙈' : '👁' }}
          </button>
        </div>
        <p v-if="validationErrors.passwordConfirm" class="field-error">
          {{ validationErrors.passwordConfirm }}
        </p>
      </div>

      <!-- 后端地址 -->
      <div class="form-field">
        <label for="backendUrl">后端地址</label>
        <input
          id="backendUrl"
          v-model="form.backendUrl"
          type="url"
          placeholder="http://localhost:8000"
          :disabled="loading"
          :class="{ 'input-error': validationErrors.backendUrl }"
          @input="clearError('backendUrl')"
        />
        <p v-if="validationErrors.backendUrl" class="field-error">
          {{ validationErrors.backendUrl }}
        </p>
        <p class="field-hint">开发环境默认 http://localhost:8000</p>
      </div>

      <!-- 提交按钮 -->
      <button type="submit" class="login-btn" :disabled="loading">
        {{ loading ? (mode === 'login' ? '登录中...' : '注册中...') : (mode === 'login' ? '登录' : '注册并登录') }}
      </button>
    </form>

    <!-- 安全说明 -->
    <p class="security-hint">
      🔒 access_token 持久化在 chrome.storage.local（扩展私有存储），关闭弹窗不丢失；
      refresh_token 由 HttpOnly Cookie 自动管理。
    </p>
  </div>
</template>

<style scoped>
.login-panel {
  padding: 8px 4px;
}

.mode-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.mode-tab {
  flex: 1;
  padding: 8px;
  background: transparent;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.2s;
}

.mode-tab:hover {
  border-color: #3b82f6;
  color: #3b82f6;
}

.mode-tab.active {
  background: #3b82f6;
  border-color: #3b82f6;
  color: white;
}

.login-title {
  margin: 0 0 16px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
  text-align: center;
}

.alert {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  margin-bottom: 12px;
}

.alert-error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.alert-success {
  background: #f0fdf4;
  color: #15803d;
  border: 1px solid #bbf7d0;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-field label {
  font-size: 12px;
  font-weight: 500;
  color: #374151;
}

.form-field input {
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
  background: white;
  transition: border-color 0.2s;
}

.form-field input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

.form-field input.input-error {
  border-color: #ef4444;
}

.password-wrapper {
  position: relative;
}

.password-wrapper input {
  width: 100%;
  padding-right: 36px;
}

.toggle-btn {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  background: transparent;
  border: none;
  padding: 4px 6px;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}

.toggle-btn:hover {
  background: #f3f4f6;
  border-radius: 4px;
}

.field-error {
  font-size: 11px;
  color: #ef4444;
  margin: 0;
}

.field-hint {
  font-size: 11px;
  color: #9ca3af;
  margin: 0;
}

.login-btn {
  padding: 10px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
  margin-top: 4px;
}

.login-btn:hover:not(:disabled) {
  background: #2563eb;
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.security-hint {
  margin: 12px 0 0;
  font-size: 10px;
  color: #9ca3af;
  text-align: center;
  line-height: 1.4;
}
</style>
