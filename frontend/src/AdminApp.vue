<script setup lang="ts">
import { ref } from 'vue'

type Overview = {
  stats: { users: number; households: number; active_batches: number; receipts: number }
  users: { id: string; email: string; display_name: string; household: string; created_at: string }[]
  households: { id: string; name: string; owner_id: string; created_at: string }[]
  receipts: { id: string; household_id: string; status: string; created_at: string }[]
}

const credentials = ref({ email: 'demo@freshloop.app', password: 'freshloop123' })
const token = ref(sessionStorage.getItem('freshloop_admin_token') ?? '')
const overview = ref<Overview | null>(null)
const error = ref('')
const loading = ref(false)

async function fetchOverview() {
  const response = await fetch('/api/admin/overview', { headers: { Authorization: `Bearer ${token.value}` } })
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail ?? '无法加载管理数据')
  overview.value = body
}

async function login() {
  loading.value = true
  error.value = ''
  try {
    const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(credentials.value) })
    const body = await response.json()
    if (!response.ok) throw new Error(body.detail ?? '登录失败')
    token.value = body.access_token
    sessionStorage.setItem('freshloop_admin_token', token.value)
    await fetchOverview()
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '登录失败' }
  finally { loading.value = false }
}

function logout() {
  token.value = ''
  overview.value = null
  sessionStorage.removeItem('freshloop_admin_token')
}

if (token.value) fetchOverview().catch(() => logout())
</script>

<template>
  <main v-if="!token || !overview" class="admin-login">
    <section class="admin-login-card"><p class="eyebrow">FreshLoop Control</p><h1>管理后台</h1><p>管理系统级用户、家庭空间、小票和库存状态。</p><form @submit.prevent="login"><label>管理员邮箱<input v-model="credentials.email" type="email" required></label><label>密码<input v-model="credentials.password" type="password" required></label><p v-if="error" class="error">{{ error }}</p><button class="primary full" :disabled="loading">{{ loading ? '登录中…' : '进入管理后台' }}</button></form><a href="http://127.0.0.1:5173">返回用户端</a></section>
  </main>
  <main v-else class="admin-shell"><aside><div class="admin-logo">FL <span>CONTROL</span></div><p>系统管理</p><button @click="fetchOverview">概览</button><button @click="logout">退出登录</button></aside><section class="admin-content"><header><div><p class="eyebrow">System overview</p><h1>FreshLoop 管理后台</h1></div><button class="secondary" @click="fetchOverview">刷新数据</button></header><div class="admin-kpis"><article><span>注册用户</span><strong>{{ overview.stats.users }}</strong></article><article><span>家庭空间</span><strong>{{ overview.stats.households }}</strong></article><article><span>在库批次</span><strong>{{ overview.stats.active_batches }}</strong></article><article><span>小票记录</span><strong>{{ overview.stats.receipts }}</strong></article></div><section class="admin-card"><h2>用户账户</h2><table><thead><tr><th>用户</th><th>邮箱</th><th>家庭</th><th>注册时间</th></tr></thead><tbody><tr v-for="user in overview.users" :key="user.id"><td>{{ user.display_name }}</td><td>{{ user.email }}</td><td>{{ user.household }}</td><td>{{ new Date(user.created_at).toLocaleString('zh-CN') }}</td></tr></tbody></table></section><section class="admin-card"><h2>最近小票</h2><div v-for="receipt in overview.receipts" :key="receipt.id" class="receipt-row"><span>{{ receipt.id.slice(0, 8) }}</span><b>{{ receipt.status }}</b><small>{{ new Date(receipt.created_at).toLocaleString('zh-CN') }}</small></div></section></section></main>
</template>
