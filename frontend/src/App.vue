<script setup lang="ts">
import { onMounted, ref } from 'vue'

const apiStatus = ref('检查中...')

onMounted(async () => {
  try {
    const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'}/api/health`)
    apiStatus.value = response.ok ? 'API 已连接' : 'API 响应异常'
  } catch {
    apiStatus.value = 'API 未连接'
  }
})
</script>

<template>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">FreshLoop v0.1.0</p>
      <h1>家庭食品管理与膳食决策</h1>
      <p class="description">从食品入库、消费记录到可解释的膳食规划，逐步建立可信的数据链路。</p>
      <span class="status">{{ apiStatus }}</span>
    </section>
  </main>
</template>

