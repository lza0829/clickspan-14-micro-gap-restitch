async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  overview: () => req('/api/overview'),
  visitors: () => req('/api/visitors'),
  sessions: (visitorId) =>
    req(visitorId ? `/api/sessions?visitor_id=${encodeURIComponent(visitorId)}` : '/api/sessions'),
  session: (id) => req(`/api/sessions/${encodeURIComponent(id)}`),
  quarantine: () => req('/api/quarantine'),
  ingest: () => req('/api/ingest/run', { method: 'POST' }),
  proofs: (sessionId) =>
    req(sessionId ? `/api/session-proofs?session_id=${encodeURIComponent(sessionId)}` : '/api/session-proofs'),
  settings: () => req('/api/settings'),
  restitchConfig: () => req('/api/restitch/config'),
  saveMicroGap: (sec) =>
    req('/api/restitch/config', { method: 'PUT', body: JSON.stringify({ micro_gap_sec: sec }) }),
  restitchCandidates: () => req('/api/restitch/candidates'),
  restitchRun: () => req('/api/restitch/run', { method: 'POST' }),
  restitchEdges: () => req('/api/restitch/edges'),
  restitchVerify: () => req('/api/restitch/verify'),
}
