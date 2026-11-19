<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const overview = ref(null)
const sessions = ref([])
const details = ref({})
const candidates = ref([])
const report = ref('')
const err = ref('')

async function load() {
  err.value = ''
  try {
    overview.value = await api.overview()
    sessions.value = await api.sessions()
    candidates.value = (await api.restitchCandidates()).candidates
    const map = {}
    for (const s of sessions.value) {
      map[s.id] = await api.session(s.id)
    }
    details.value = map
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function runRestitch() {
  err.value = ''
  report.value = ''
  try {
    const res = await api.restitchRun()
    if (res.restitched.length) {
      report.value =
        `已缝回 ${res.restitched.length} 对：` +
        res.restitched
          .map((r) => `${r.swallowed_session_id} → ${r.retained_session_id}（间隙 ${r.gap_sec}s，迁移 ${r.moved_hit_count} 点）`)
          .join('；')
    } else {
      report.value = '无误切候选，织机已收敛'
    }
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="stats" v-if="overview">
      <div class="stat"><b>{{ overview.visitors }}</b><span>访客</span></div>
      <div class="stat"><b>{{ overview.sessions }}</b><span>会话</span></div>
      <div class="stat"><b>{{ overview.hits_primary }}</b><span>正式击点</span></div>
      <div class="stat"><b>{{ overview.quarantine }}</b><span>隔离</span></div>
      <div class="stat"><b>{{ overview.idle_timeout_sec }}s</b><span>空闲超时</span></div>
      <div class="stat"><b>{{ overview.micro_gap_sec }}s</b><span>微间隙</span></div>
      <div class="stat"><b>{{ overview.restitch_edges }}</b><span>缝回边</span></div>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <section class="panel">
      <h1>会话织机</h1>
      <p class="muted">
        同访客相邻两段间隙 ≤ 微间隙即误切，可强制缝回；缝回后被吞段撤下织机，仅保留会话对外可查。
      </p>
      <div class="restitch-bar">
        <span>误切候选 <b>{{ candidates.length }}</b> 对</span>
        <button class="btn" :disabled="!candidates.length" @click="runRestitch">扫描并缝回</button>
        <span v-if="report" class="ok">{{ report }}</span>
      </div>
      <div class="lane-list">
        <article v-for="s in sessions" :key="s.id" class="lane">
          <div class="lane-head">
            <div>
              <strong>{{ s.id }}</strong>
              <span class="muted"> · {{ s.visitor_id }} · {{ s.status }}</span>
            </div>
            <span class="mono">{{ s.hit_count }} 点 · {{ s.started_at }} → {{ s.ended_at || '…' }}</span>
          </div>
          <div class="beads" v-if="details[s.id]">
            <template v-for="(h, i) in details[s.id].hits" :key="h.id">
              <span v-if="i" class="connector" aria-hidden="true"></span>
              <span class="hit-bead">
                <strong>{{ h.event_name }}</strong>
                <span>{{ h.page_path }}</span>
              </span>
            </template>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.restitch-bar {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  margin-bottom: 0.9rem;
}
.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
