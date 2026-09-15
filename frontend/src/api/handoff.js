import { BASE_URL } from './chat'

function wsBaseUrl() {
  const api = BASE_URL.replace(/\/api\/v1\/?$/, '')
  return api.replace(/^http/, 'ws')
}

function apiBaseUrl() {
  return BASE_URL.replace(/\/api\/v1\/?$/, '')
}

export function parseApiError(err, fallback = 'Request failed') {
  if (!err) return fallback
  if (typeof err === 'string') return err
  const detail = err.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || item.message || String(item)).join(', ')
  }
  if (err.message) return err.message
  return fallback
}

export async function requestHandoff(sessionId, reason = '') {
  const res = await fetch(`${BASE_URL}/handoff/request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, reason }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(parseApiError(err, 'Could not request human agent'))
  }
  return res.json()
}

export async function getHandoffStatus(sessionId) {
  const res = await fetch(`${BASE_URL}/handoff/status/${encodeURIComponent(sessionId)}`)
  if (!res.ok) throw new Error('Could not get handoff status')
  return res.json()
}

export async function sendHandoffMessage(sessionId, message) {
  const res = await fetch(`${BASE_URL}/handoff/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(parseApiError(err, 'Could not send message'))
  }
  return res.json()
}

export async function resolveHandoff(sessionId) {
  const res = await fetch(`${BASE_URL}/handoff/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
  if (!res.ok) throw new Error('Could not resolve handoff')
  return res.json()
}

export async function submitFeedback(sessionId, rating, comment = '') {
  const res = await fetch(`${BASE_URL}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, rating, comment }),
  })
  if (!res.ok) throw new Error('Could not submit feedback')
  return res.json()
}

export function connectHandoffWebSocket(sessionId, { onEvent, onClose, onOpen, onError }) {
  const url = `${wsBaseUrl()}/ws/handoff/${encodeURIComponent(sessionId)}`
  const ws = new WebSocket(url)
  ws.onopen = () => onOpen?.()
  ws.onerror = () => onError?.()
  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data)
      onEvent?.(payload.event, payload.data)
    } catch {
      // ignore malformed events
    }
  }
  ws.onclose = () => onClose?.()
  return ws
}

export function connectHandoffEventSource(sessionId, { onEvent }) {
  const url = `${apiBaseUrl()}/api/v1/handoff/stream/${encodeURIComponent(sessionId)}`
  const es = new EventSource(url)
  es.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data)
      onEvent?.(payload.event, payload.data)
    } catch {
      // ignore malformed events
    }
  }
  return es
}
