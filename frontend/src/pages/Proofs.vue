<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const edges = ref([])
const verify = ref(null)
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await api.proofs()
    edges.value = await api.restitchEdges()
    verify.value = await api.restitchVerify()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <section class="panel">
    <h1>会话核证</h1>
    <p class="muted">会话击点集合与起止须与织机同源可核。</p>
    <p v-if="err" class="err">{{ err }}</p>
    <table class="data">
      <thead>
        <tr><th>会话</th><th>访客</th><th>点数</th><th>起止</th><th>击点集</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td class="mono">{{ r.session_id }}</td>
          <td>{{ r.visitor_id }}</td>
          <td>{{ r.hit_count }}</td>
          <td class="mono">{{ r.started_at }} → {{ r.ended_at }}</td>
          <td class="mono">{{ r.hit_ids_json }}</td>
        </tr>
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h1>缝回核证</h1>
    <p class="muted">只读核对：被吞关闭、成员归并、边界同源、无残留微间隙。</p>
    <p v-if="verify" :class="verify.ok ? 'ok' : 'err'">
      {{ verify.ok ? '核证通过' : '核证未通过' }} · 边 {{ verify.edges_checked }} 条 · 残留候选 {{ verify.residual_candidates }} 对
    </p>
    <table class="data" v-if="edges.length">
      <thead>
        <tr><th>被吞会话</th><th>保留会话</th><th>访客</th><th>微间隙</th><th>迁移点数</th><th>缝回时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="e in edges" :key="e.id">
          <td class="mono">{{ e.swallowed_session_id }}</td>
          <td class="mono">{{ e.retained_session_id }}</td>
          <td>{{ e.visitor_id }}</td>
          <td class="mono">{{ e.gap_sec }}s</td>
          <td>{{ e.moved_hit_count }}</td>
          <td class="mono">{{ e.created_at }}</td>
        </tr>
      </tbody>
    </table>
    <table class="data" v-if="verify">
      <thead>
        <tr><th>核对项</th><th>结果</th><th>明细</th></tr>
      </thead>
      <tbody>
        <tr v-for="c in verify.checks" :key="c.check">
          <td>{{ c.check }}</td>
          <td :class="c.ok ? 'ok' : 'err'">{{ c.ok ? '通过' : '失败' }}</td>
          <td class="mono">{{ c.detail }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
