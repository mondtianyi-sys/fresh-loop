<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

type Food = { id: string; name: string; category: string; default_unit: string }
type Batch = { id: string; food_item_id: string; food_name: string; category: string; quantity: number; remaining_quantity: number; unit: string; purchased_on: string; expires_on: string | null; price: number | null }
type Ledger = { id: string; food_name: string; event_type: string; quantity_delta: number; note: string; created_at: string }
type Dashboard = { active_batches: number; total_quantity: number; expiring_batches: number; expired_batches: number; inventory_value: number }
type Analytics = { consumed: number; discarded: number; waste_rate: number; daily: { date: string; consume: number; discard: number }[]; by_food: { name: string; consume: number; discard: number }[] }
type Plan = { id: string; status: string; days?: number; people?: number; budget: number; estimated_purchase: number; expiring_used: string[]; warnings: string[]; menu: { day: number; title: string; cooking_minutes: number; ingredients: { name: string; amount: number; unit: string }[] }[] }
type ReceiptLine = { id?: string; name: string; quantity: number; unit: string; price: number | null; confidence?: number; category?: string }
type Receipt = { id: string; status: string; created_at?: string; lines: ReceiptLine[] }
type Citation = { title: string; source_url: string | null; excerpt: string }

const API = import.meta.env.VITE_API_BASE_URL ?? ''
const token = ref(localStorage.getItem('freshloop_token') ?? '')
const profile = reactive({ name: localStorage.getItem('freshloop_name') ?? '', household: localStorage.getItem('freshloop_household') ?? '' })
const mode = ref<'login' | 'register'>('login')
const busy = ref(false)
const message = ref('')
const error = ref('')
const view = ref<'inventory' | 'ledger' | 'analytics' | 'planner' | 'receipts' | 'assistant'>('inventory')

const auth = reactive({ email: 'demo@freshloop.app', password: 'freshloop123', display_name: '体验用户', household_name: '我的家庭' })
const foods = ref<Food[]>([])
const inventory = ref<Batch[]>([])
const ledger = ref<Ledger[]>([])
const stats = reactive<Dashboard>({ active_batches: 0, total_quantity: 0, expiring_batches: 0, expired_batches: 0, inventory_value: 0 })
const analyticsData = reactive<Analytics>({ consumed: 0, discarded: 0, waste_rate: 0, daily: [], by_food: [] })
const plans = ref<Plan[]>([])
const planForm = reactive({ days: 3, people: 2, budget: 100, dislikesText: '', max_cooking_minutes: 30 })
const planning = ref(false)
const receipts = ref<Receipt[]>([])
const receiptText = ref('西红柿 2 个 8.50\n鸡蛋 10 枚 15.80\n鲜牛奶 2 盒 12.00')
const parsingReceipt = ref(false)
const confirmingReceipt = ref(false)
const receiptImage = ref<File | null>(null)
const uploadingReceiptImage = ref(false)
const question = ref('有哪些临期食品？')
const answer = ref('')
const citations = ref<Citation[]>([])
const asking = ref(false)
const foodForm = reactive({ name: '', category: '蔬菜', default_unit: '份' })
const batchForm = reactive({ food_item_id: '', quantity: 1, unit: '份', purchased_on: new Date().toISOString().slice(0, 10), expires_on: '', price: null as number | null })
const showFoodForm = ref(false)
const showBatchForm = ref(false)
const consumeTarget = ref<Batch | null>(null)
const consumeForm = reactive({ quantity: 1, event_type: 'consume', note: '' })

const isAuthenticated = computed(() => Boolean(token.value))

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token.value ? { Authorization: `Bearer ${token.value}` } : {}), ...options.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

function flash(text: string) {
  message.value = text
  setTimeout(() => { if (message.value === text) message.value = '' }, 2600)
}

async function submitAuth() {
  busy.value = true
  error.value = ''
  try {
    const path = mode.value === 'login' ? '/api/auth/login' : '/api/auth/register'
    const payload = mode.value === 'login' ? { email: auth.email, password: auth.password } : auth
    const data = await request<{ access_token: string; user: { display_name: string }; household: { name: string } }>(path, { method: 'POST', body: JSON.stringify(payload) })
    token.value = data.access_token
    profile.name = data.user.display_name
    profile.household = data.household.name
    localStorage.setItem('freshloop_token', token.value)
    localStorage.setItem('freshloop_name', profile.name)
    localStorage.setItem('freshloop_household', profile.household)
    await loadAll()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法登录'
  } finally { busy.value = false }
}

function logout() {
  token.value = ''
  localStorage.removeItem('freshloop_token')
  localStorage.removeItem('freshloop_name')
  localStorage.removeItem('freshloop_household')
}

async function exportData() {
  try {
    const response = await fetch(`${API}/api/export`, { headers: { Authorization: `Bearer ${token.value}` } })
    if (!response.ok) throw new Error('导出失败')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `freshloop-${new Date().toISOString().slice(0, 10)}.json`
    link.click()
    URL.revokeObjectURL(url)
    flash('数据文件已开始下载')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '导出失败' }
}

async function loadAll() {
  try {
    const [foodData, inventoryData, ledgerData, dashboardData, analysis, planData, receiptData] = await Promise.all([
      request<Food[]>('/api/foods'), request<Batch[]>('/api/inventory'), request<Ledger[]>('/api/ledger'), request<Dashboard>('/api/dashboard'), request<Analytics>('/api/analytics'), request<Plan[]>('/api/plans'), request<Receipt[]>('/api/receipts'),
    ])
    foods.value = foodData
    inventory.value = inventoryData
    ledger.value = ledgerData
    Object.assign(stats, dashboardData)
    Object.assign(analyticsData, analysis)
    plans.value = planData
    receipts.value = receiptData
    if (!batchForm.food_item_id && foods.value[0]) chooseFood(foods.value[0].id)
  } catch (reason) {
    if (reason instanceof Error && reason.message.includes('登录')) logout()
    else error.value = reason instanceof Error ? reason.message : '数据加载失败'
  }
}

function chooseFood(id: string) {
  batchForm.food_item_id = id
  const food = foods.value.find((item) => item.id === id)
  if (food) batchForm.unit = food.default_unit
}

async function createFood() {
  try {
    const created = await request<Food>('/api/foods', { method: 'POST', body: JSON.stringify(foodForm) })
    foods.value.push(created)
    chooseFood(created.id)
    foodForm.name = ''
    showFoodForm.value = false
    flash('食品档案已创建')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '创建失败' }
}

async function deleteFood(food: Food) {
  if (!window.confirm(`删除食品档案“${food.name}”？`)) return
  try {
    await request(`/api/foods/${food.id}`, { method: 'DELETE' })
    await loadAll()
    flash('食品档案已删除')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '删除失败' }
}

async function addBatch() {
  try {
    await request('/api/inventory', { method: 'POST', body: JSON.stringify({ ...batchForm, expires_on: batchForm.expires_on || null }) })
    showBatchForm.value = false
    await loadAll()
    flash('入库成功，流水已记录')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '入库失败' }
}

function openConsume(batch: Batch) {
  consumeTarget.value = batch
  consumeForm.quantity = Math.min(1, batch.remaining_quantity)
  consumeForm.event_type = 'consume'
  consumeForm.note = ''
}

async function submitConsume() {
  if (!consumeTarget.value) return
  try {
    await request(`/api/inventory/${consumeTarget.value.id}/consume`, { method: 'POST', body: JSON.stringify(consumeForm) })
    consumeTarget.value = null
    await loadAll()
    flash(consumeForm.event_type === 'consume' ? '消费已记录' : '丢弃已记录')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '操作失败' }
}

async function deleteBatch(batch: Batch) {
  if (!window.confirm(`删除“${batch.food_name}”这笔入库记录？`)) return
  try {
    await request(`/api/inventory/${batch.id}`, { method: 'DELETE' })
    await loadAll()
    flash('入库记录已删除')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '删除失败' }
}

async function generatePlan() {
  planning.value = true
  error.value = ''
  try {
    const dislikes = planForm.dislikesText.split(/[，,]/).map((item) => item.trim()).filter(Boolean)
    const plan = await request<Plan>('/api/plans', { method: 'POST', body: JSON.stringify({ ...planForm, dislikes }) })
    plans.value.unshift(plan)
    flash('膳食计划草稿已生成')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '计划生成失败' }
  finally { planning.value = false }
}

async function confirmPlan(plan: Plan) {
  await request(`/api/plans/${plan.id}/confirm`, { method: 'POST' })
  plan.status = 'confirmed'
  flash('计划已确认')
}

async function deletePlan(plan: Plan) {
  if (!window.confirm('删除这份膳食计划？')) return
  try {
    await request(`/api/plans/${plan.id}`, { method: 'DELETE' })
    plans.value = plans.value.filter((item) => item.id !== plan.id)
    flash('膳食计划已删除')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '删除失败' }
}

function barHeight(value: number) {
  const max = Math.max(1, ...analyticsData.daily.map((item) => item.consume + item.discard))
  return `${Math.max(4, value / max * 100)}%`
}

async function askAssistant() {
  asking.value = true
  try {
    const data = await request<{ answer: string; citations: Citation[] }>('/api/assistant', { method: 'POST', body: JSON.stringify({ question: question.value }) })
    answer.value = data.answer
    citations.value = data.citations
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '助手暂时不可用' }
  finally { asking.value = false }
}


async function parseReceipt() {
  parsingReceipt.value = true
  error.value = ''
  try {
    const receipt = await request<Receipt>('/api/receipts/parse', { method: 'POST', body: JSON.stringify({ raw_text: receiptText.value }) })
    receipts.value.unshift(receipt)
    flash('小票草稿已生成，请核对后确认入库')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '小票解析失败' }
  finally { parsingReceipt.value = false }
}

async function uploadReceiptImage() {
  if (!receiptImage.value) return
  uploadingReceiptImage.value = true
  error.value = ''
  try {
    const form = new FormData()
    form.append('image', receiptImage.value)
    const response = await fetch(`${API}/api/receipts/image`, { method: 'POST', headers: { Authorization: `Bearer ${token.value}` }, body: form })
    const body = await response.json()
    if (!response.ok) throw new Error(body.detail ?? '照片识别失败')
    receipts.value.unshift(body as Receipt)
    flash('照片已识别为小票草稿，请核对后确认入库')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '照片上传失败' }
  finally { uploadingReceiptImage.value = false }
}

async function confirmReceipt(receipt: Receipt) {
  confirmingReceipt.value = true
  try {
    await request(`/api/receipts/${receipt.id}/confirm`, { method: 'POST', body: JSON.stringify({ purchased_on: new Date().toISOString().slice(0, 10), expires_on: null, lines: receipt.lines.map((line) => ({ ...line, category: line.category ?? '其他' })) }) })
    receipt.status = 'confirmed'
    await loadAll()
    flash('小票已确认，食品和库存已写入')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '确认入库失败' }
  finally { confirmingReceipt.value = false }
}

async function deleteReceipt(receipt: Receipt) {
  if (!window.confirm('删除这张小票草稿？')) return
  try {
    await request(`/api/receipts/${receipt.id}`, { method: 'DELETE' })
    receipts.value = receipts.value.filter((item) => item.id !== receipt.id)
    flash('小票草稿已删除')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '删除失败' }
}

function expiryLabel(value: string | null) {
  if (!value) return '未设置'
  const days = Math.ceil((new Date(value).getTime() - new Date().setHours(0, 0, 0, 0)) / 86400000)
  if (days < 0) return `已过期 ${Math.abs(days)} 天`
  if (days === 0) return '今天到期'
  return `${days} 天后到期`
}

onMounted(() => { if (token.value) loadAll() })
</script>

<template>
  <main v-if="!isAuthenticated" class="auth-page">
    <section class="brand-panel">
      <div class="brand-mark">FL</div>
      <p class="eyebrow">FreshLoop</p>
      <h1>让每一份食物<br>都有清晰去向</h1>
      <p>记录库存、关注临期、减少浪费。你的家庭食品管理，从这里开始。</p>
      <div class="brand-stats"><span><b>清晰</b>批次库存</span><span><b>可信</b>变动流水</span><span><b>及时</b>临期提醒</span></div>
    </section>
    <section class="auth-card">
      <div><p class="eyebrow">{{ mode === 'login' ? '欢迎回来' : '开始使用' }}</p><h2>{{ mode === 'login' ? '登录 FreshLoop' : '创建家庭空间' }}</h2><p class="muted">{{ mode === 'login' ? '继续管理你的家庭食品库存' : '注册后会自动创建第一个家庭空间' }}</p></div>
      <form @submit.prevent="submitAuth">
        <label v-if="mode === 'register'">你的称呼<input v-model="auth.display_name" required></label>
        <label v-if="mode === 'register'">家庭名称<input v-model="auth.household_name" required></label>
        <label>邮箱<input v-model="auth.email" type="email" required></label>
        <label>密码<input v-model="auth.password" type="password" minlength="8" required></label>
        <p v-if="error" class="error">{{ error }}</p>
        <button class="primary full" :disabled="busy">{{ busy ? '请稍候…' : mode === 'login' ? '登录' : '创建账户' }}</button>
      </form>
      <button class="text-button" @click="mode = mode === 'login' ? 'register' : 'login'; error = ''">{{ mode === 'login' ? '还没有账户？立即注册' : '已有账户？返回登录' }}</button>
    </section>
  </main>

  <main v-else class="app-shell">
    <aside class="sidebar">
      <div class="logo"><span>FL</span><strong>FreshLoop</strong></div>
      <nav><button :class="{ active: view === 'inventory' }" @click="view = 'inventory'">⌂ <span>库存总览</span></button><button :class="{ active: view === 'ledger' }" @click="view = 'ledger'">↕ <span>库存流水</span></button><button :class="{ active: view === 'receipts' }" @click="view = 'receipts'">▣ <span>小票导入</span></button><button :class="{ active: view === 'analytics' }" @click="view = 'analytics'">⌁ <span>消费分析</span></button><button :class="{ active: view === 'planner' }" @click="view = 'planner'">□ <span>膳食计划</span></button><button :class="{ active: view === 'assistant' }" @click="view = 'assistant'">✦ <span>智能助手</span></button></nav>
      <div class="user-block"><div class="avatar">{{ profile.name.slice(0, 1) }}</div><div><strong>{{ profile.name }}</strong><small>{{ profile.household }}</small></div><button title="退出" @click="logout">↗</button></div>
    </aside>

    <section class="workspace">
      <header><div><p class="eyebrow">{{ profile.household }}</p><h1>{{ { inventory: '库存总览', ledger: '库存流水', receipts: '小票导入', analytics: '消费分析', planner: '膳食计划', assistant: '智能助手' }[view] }}</h1></div><div class="header-actions"><button class="secondary" @click="exportData">↓ 导出数据</button><template v-if="view === 'inventory'"><button class="secondary" @click="showFoodForm = true">＋ 食品档案</button><button class="primary" :disabled="!foods.length" @click="showBatchForm = true">＋ 新增入库</button></template></div></header>
      <p v-if="message" class="toast">✓ {{ message }}</p><p v-if="error" class="error banner" @click="error = ''">{{ error }} ×</p>

      <template v-if="view === 'inventory'">
        <div class="stat-grid">
          <article><span>在库批次</span><strong>{{ stats.active_batches }}</strong><small>当前仍有余量</small></article>
          <article><span>库存总量</span><strong>{{ stats.total_quantity }}</strong><small>跨单位统计</small></article>
          <article class="warning"><span>3 天内临期</span><strong>{{ stats.expiring_batches }}</strong><small>建议优先使用</small></article>
          <article class="danger"><span>已过期</span><strong>{{ stats.expired_batches }}</strong><small>请及时检查</small></article>
          <article><span>剩余价值</span><strong>¥{{ stats.inventory_value.toFixed(2) }}</strong><small>按剩余比例估算</small></article>
        </div>
        <section class="panel">
          <div class="panel-heading"><div><h2>当前库存</h2><p>按到期时间优先排列</p></div><span>{{ inventory.length }} 个批次</span></div>
          <div v-if="inventory.length" class="table-wrap"><table><thead><tr><th>食品</th><th>分类</th><th>剩余</th><th>购买日期</th><th>到期状态</th><th></th></tr></thead><tbody><tr v-for="batch in inventory" :key="batch.id"><td><strong>{{ batch.food_name }}</strong></td><td><span class="tag">{{ batch.category }}</span></td><td>{{ batch.remaining_quantity }} / {{ batch.quantity }} {{ batch.unit }}</td><td>{{ batch.purchased_on }}</td><td><span :class="['expiry', { urgent: batch.expires_on && new Date(batch.expires_on) <= new Date(Date.now() + 3 * 86400000) }]">{{ expiryLabel(batch.expires_on) }}</span></td><td><button class="small-button" @click="openConsume(batch)">记录消耗</button><button class="danger-button" @click="deleteBatch(batch)">删除</button></td></tr></tbody></table></div>
          <div v-else class="empty"><span>◌</span><h3>库存还是空的</h3><p>先创建食品档案，再记录第一笔入库。</p><button class="primary" @click="showFoodForm = true">创建食品档案</button></div>
        </section>
      </template>

      <section v-else-if="view === 'ledger'" class="panel ledger-panel">
        <div class="panel-heading"><div><h2>最近变动</h2><p>入库、消费和丢弃记录不可变更</p></div><span>{{ ledger.length }} 条</span></div>
        <div v-if="ledger.length" class="timeline"><article v-for="row in ledger" :key="row.id"><span :class="['event-dot', row.event_type]"></span><div><strong>{{ row.food_name }}</strong><p>{{ row.event_type === 'inbound' ? '入库' : row.event_type === 'consume' ? '消费' : '丢弃' }} · {{ row.note || '无备注' }}</p></div><div class="event-value" :class="{ positive: row.quantity_delta > 0 }">{{ row.quantity_delta > 0 ? '+' : '' }}{{ row.quantity_delta }}<small>{{ new Date(row.created_at).toLocaleString('zh-CN') }}</small></div></article></div>
        <div v-else class="empty"><span>↕</span><h3>暂无库存流水</h3><p>新增一笔入库后，变动会记录在这里。</p></div>
      </section>

      <div v-else-if="view === 'receipts'" class="receipt-grid">
        <form class="panel receipt-form" @submit.prevent="parseReceipt"><div class="panel-heading"><div><h2>导入小票</h2><p>照片自动识别，或粘贴 OCR 文本</p></div></div><div class="receipt-fields"><label>小票照片<input accept="image/jpeg,image/png,image/webp" type="file" @change="receiptImage = ($event.target as HTMLInputElement).files?.[0] ?? null"></label><button type="button" class="secondary full" :disabled="!receiptImage || uploadingReceiptImage" @click="uploadReceiptImage">{{ uploadingReceiptImage ? '正在识别照片…' : '上传照片并识别' }}</button><label>商品行<textarea v-model="receiptText" rows="7" required></textarea></label><p class="format-hint">每行格式：食品名称 数量 单位 价格<br>例如：西红柿 2 个 8.50</p><button class="primary full" :disabled="parsingReceipt">{{ parsingReceipt ? '正在解析…' : '解析文字为草稿' }}</button></div></form>
        <section class="receipt-results"><article v-for="receipt in receipts" :key="receipt.id" class="panel receipt-card"><div class="plan-card-head"><div><span :class="['status-tag', receipt.status]">{{ receipt.status === 'confirmed' ? '已入库' : '待确认' }}</span><h2>小票草稿</h2><p>{{ receipt.created_at ? new Date(receipt.created_at).toLocaleString('zh-CN') : '刚刚生成' }} · {{ receipt.lines.length }} 项商品</p></div><div class="inline-actions"><button v-if="receipt.status === 'draft'" class="primary" :disabled="confirmingReceipt" @click="confirmReceipt(receipt)">确认入库</button><button v-if="receipt.status === 'draft'" class="danger-button" @click="deleteReceipt(receipt)">删除</button></div></div><div class="receipt-lines"><div v-for="line in receipt.lines" :key="line.id"><input v-model="line.name"><input v-model.number="line.quantity" type="number" min="0.01" step="0.01"><input v-model="line.unit"><input v-model.number="line.price" type="number" min="0" step="0.01"><small>{{ Math.round((line.confidence ?? 0.8) * 100) }}% 置信度</small></div></div></article><div v-if="!receipts.length" class="panel empty"><span>▣</span><h3>还没有小票草稿</h3><p>粘贴一段商品文字，即可生成可编辑的入库草稿。</p></div></section>
      </div>

      <template v-else-if="view === 'analytics'">
        <div class="analysis-summary"><article><span>14 天消费量</span><strong>{{ analyticsData.consumed }}</strong></article><article><span>14 天丢弃量</span><strong>{{ analyticsData.discarded }}</strong></article><article><span>浪费率</span><strong>{{ analyticsData.waste_rate }}%</strong></article></div>
        <div class="analysis-grid">
          <section class="panel chart-panel"><div class="panel-heading"><div><h2>消费与浪费趋势</h2><p>最近 14 天库存变动</p></div></div><div class="bar-chart"><div v-for="day in analyticsData.daily" :key="day.date" class="bar-column"><div class="bars"><i class="discard-bar" :style="{ height: barHeight(day.discard) }"></i><i class="consume-bar" :style="{ height: barHeight(day.consume) }"></i></div><small>{{ day.date.slice(5) }}</small></div></div><div class="legend"><span><i class="consume-dot"></i>消费</span><span><i class="discard-dot"></i>丢弃</span></div></section>
          <section class="panel ranking"><div class="panel-heading"><div><h2>食品消耗排行</h2><p>按消费和丢弃总量</p></div></div><div v-if="analyticsData.by_food.length"><article v-for="(item, index) in analyticsData.by_food" :key="item.name"><b>{{ index + 1 }}</b><span>{{ item.name }}</span><strong>{{ item.consume + item.discard }}</strong></article></div><div v-else class="empty compact"><p>记录消费后即可查看排行</p></div></section>
        </div>
      </template>

      <section v-else-if="view === 'assistant'" class="panel assistant-panel"><div class="assistant-answer"><p class="eyebrow">受控数据查询</p><h2>{{ answer || '问问你的库存和临期食品' }}</h2><p v-if="!answer" class="muted">例如：有哪些临期食品？我现在有什么食材？</p><div v-for="citation in citations" :key="citation.title" class="citation"><strong>{{ citation.title }}</strong><p>{{ citation.excerpt }}</p></div></div><form class="assistant-form" @submit.prevent="askAssistant"><input v-model="question" placeholder="输入问题" required><button class="primary" :disabled="asking">{{ asking ? '查询中…' : '发送' }}</button></form></section>


      <div v-else class="planner-grid">
        <form class="panel plan-form" @submit.prevent="generatePlan"><div class="panel-heading"><div><h2>生成新计划</h2><p>优先使用临期和现有库存</p></div></div><div class="plan-fields"><div class="form-row"><label>计划天数<input v-model.number="planForm.days" type="number" min="1" max="7"></label><label>用餐人数<input v-model.number="planForm.people" type="number" min="1" max="10"></label></div><label>采购预算（元）<input v-model.number="planForm.budget" type="number" min="0"></label><label>不喜欢的食物<input v-model="planForm.dislikesText" placeholder="用逗号分隔，例如：香菜，芹菜"></label><label>最长烹饪时间（分钟）<input v-model.number="planForm.max_cooking_minutes" type="number" min="5" max="180"></label><button class="primary full" :disabled="planning">{{ planning ? '正在规划…' : '生成计划草稿' }}</button></div></form>
        <section class="plan-results"><article v-for="plan in plans" :key="plan.id" class="panel plan-card"><div class="plan-card-head"><div><span :class="['status-tag', plan.status]">{{ plan.status === 'confirmed' ? '已确认' : '草稿' }}</span><h2>{{ plan.menu.length }} 天膳食计划</h2><p>预计采购 ¥{{ plan.estimated_purchase }} / 预算 ¥{{ plan.budget }}</p></div><div class="inline-actions"><button v-if="plan.status !== 'confirmed'" class="secondary" @click="confirmPlan(plan)">确认计划</button><button class="danger-button" @click="deletePlan(plan)">删除</button></div></div><p v-for="warning in plan.warnings" :key="warning" class="plan-warning">{{ warning }}</p><p v-if="plan.expiring_used.length" class="plan-good">优先使用临期：{{ plan.expiring_used.join('、') }}</p><div class="menu-list"><div v-for="day in plan.menu" :key="day.day"><b>第 {{ day.day }} 天</b><span>{{ day.title }}</span><small>{{ day.cooking_minutes }} 分钟 · {{ day.ingredients.map(item => item.name).join('、') || '待采购' }}</small></div></div></article><div v-if="!plans.length" class="panel empty"><span>□</span><h3>还没有膳食计划</h3><p>设置人数、天数和预算，生成第一份草稿。</p></div></section>
      </div>
    </section>

    <div v-if="showFoodForm || showBatchForm || consumeTarget" class="modal-backdrop" @click.self="showFoodForm = false; showBatchForm = false; consumeTarget = null">
      <form v-if="showFoodForm" class="modal" @submit.prevent="createFood"><div class="modal-title"><div><p class="eyebrow">食品档案</p><h2>添加一种食品</h2></div><button type="button" @click="showFoodForm = false">×</button></div><label>食品名称<input v-model="foodForm.name" placeholder="例如：西红柿" required autofocus></label><div class="form-row"><label>分类<select v-model="foodForm.category"><option>蔬菜</option><option>水果</option><option>肉蛋奶</option><option>主食</option><option>调味品</option><option>其他</option></select></label><label>默认单位<input v-model="foodForm.default_unit" required></label></div><button class="primary full">保存食品档案</button></form>
      <form v-else-if="showBatchForm" class="modal" @submit.prevent="addBatch"><div class="modal-title"><div><p class="eyebrow">库存</p><h2>新增入库</h2></div><button type="button" @click="showBatchForm = false">×</button></div><label>食品<select v-model="batchForm.food_item_id" required @change="chooseFood(batchForm.food_item_id)"><option v-for="food in foods" :key="food.id" :value="food.id">{{ food.name }}</option></select></label><div class="form-row"><label>数量<input v-model.number="batchForm.quantity" type="number" min="0.01" step="0.01" required></label><label>单位<input v-model="batchForm.unit" required></label></div><div class="form-row"><label>购买日期<input v-model="batchForm.purchased_on" type="date" required></label><label>到期日期<input v-model="batchForm.expires_on" type="date"></label></div><label>本批价格（元）<input v-model.number="batchForm.price" type="number" min="0" step="0.01" placeholder="选填"></label><button class="primary full">确认入库</button></form>
      <form v-else-if="consumeTarget" class="modal" @submit.prevent="submitConsume"><div class="modal-title"><div><p class="eyebrow">库存变动</p><h2>{{ consumeTarget.food_name }}</h2></div><button type="button" @click="consumeTarget = null">×</button></div><p class="stock-hint">当前剩余 {{ consumeTarget.remaining_quantity }} {{ consumeTarget.unit }}</p><div class="form-row"><label>类型<select v-model="consumeForm.event_type"><option value="consume">消费</option><option value="discard">丢弃</option></select></label><label>数量<input v-model.number="consumeForm.quantity" type="number" min="0.01" :max="consumeTarget.remaining_quantity" step="0.01" required></label></div><label>备注<input v-model="consumeForm.note" placeholder="例如：晚餐使用"></label><button class="primary full">记录变动</button></form>
    </div>
  </main>
</template>
