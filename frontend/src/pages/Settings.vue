<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const sec = ref(1800)
const micro = ref(120)
const msgIdle = ref('')
const errIdle = ref('')
const msgMicro = ref('')
const errMicro = ref('')

onMounted(async () => {
  try {
    const s = await api.settings()
    sec.value = s.idle_timeout_sec
    micro.value = s.micro_gap_sec
  } catch (e) {
    errIdle.value = String(e.message || e)
  }
})

async function save() {
  errIdle.value = ''
  msgIdle.value = ''
  try {
    const res = await fetch('/api/settings/idle-timeout', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idle_timeout_sec: Number(sec.value) }),
    })
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    sec.value = data.idle_timeout_sec
    msgIdle.value = '已保存（基座装载尚未按此时切开）'
  } catch (e) {
    errIdle.value = String(e.message || e)
  }
}

async function saveMicro() {
  errMicro.value = ''
  msgMicro.value = ''
  try {
    const data = await api.saveMicroGap(Number(micro.value))
    micro.value = data.micro_gap_sec
    msgMicro.value = '微间隙已保存，织机扫描即按此缝回'
  } catch (e) {
    errMicro.value = String(e.message || e)
  }
}
</script>

<template>
  <section class="panel">
    <h1>空闲切窗</h1>
    <p class="muted">配置写入 settings；真正按间隙切会话由功能题空桩实现。</p>
    <label>
      秒数
      <input v-model.number="sec" type="number" min="60" step="60" class="mono" />
    </label>
    <p>
      <button class="btn" @click="save">保存</button>
    </p>
    <p v-if="msgIdle" class="ok">{{ msgIdle }}</p>
    <p v-if="errIdle" class="err">{{ errIdle }}</p>
  </section>

  <section class="panel">
    <h1>微间隙缝回</h1>
    <p class="muted">误切判定上限，须为正且严格小于空闲超时；织机扫描按此缝回。</p>
    <label>
      微间隙秒数
      <input v-model.number="micro" type="number" min="1" step="1" class="mono" />
    </label>
    <p>
      <button class="btn" @click="saveMicro">保存微间隙</button>
    </p>
    <p v-if="msgMicro" class="ok">{{ msgMicro }}</p>
    <p v-if="errMicro" class="err">{{ errMicro }}</p>
  </section>
</template>
