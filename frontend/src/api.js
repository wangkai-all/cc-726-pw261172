// Thin fetch wrapper around the FastAPI backend. All endpoints live under
// /api, which Vite proxies to 127.0.0.1:8000 in development.

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: options.body instanceof FormData
      ? {}
      : { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = typeof body.detail === 'string'
        ? body.detail
        : JSON.stringify(body.detail)
    } catch (_) { /* keep status text */ }
    const err = new Error(`HTTP ${resp.status}: ${detail}`)
    err.status = resp.status
    throw err
  }
  if (resp.status === 204) return null
  return resp.json()
}

export const api = {
  health: () => request('/api/health'),
  upload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/api/datasets', { method: 'POST', body: form })
  },
  listDatasets: () => request('/api/datasets'),
  getDataset: (id) => request(`/api/datasets/${id}`),
  getRecord: (id, no) =>
    request(`/api/datasets/${id}/records/${no}`),
  rawRecordUrl: (id, no) =>
    `/api/datasets/${id}/records/${no}/raw`,
  listSchemes: (id) => request(`/api/datasets/${id}/schemes`),
  saveScheme: (id, name, scheme) =>
    request(`/api/datasets/${id}/schemes/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ scheme, activate: true }),
    }),
  preview: (id, recordNo, schemeName) => {
    const qs = schemeName ? `?scheme=${encodeURIComponent(schemeName)}` : ''
    return request(`/api/datasets/${id}/preview/${recordNo}${qs}`)
  },
  previewDraft: (id, recordNo, schemeDraft) =>
    request(`/api/datasets/${id}/preview/${recordNo}`, {
      method: 'POST',
      body: JSON.stringify({ scheme: schemeDraft }),
    }),
  exportDataset: (id, format, scheme) =>
    request(`/api/datasets/${id}/export`, {
      method: 'POST',
      body: JSON.stringify({ format, scheme }),
    }),
}
