async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    if (res.status === 429) throw new Error('Too many requests — please wait a moment and try again.')
    const body = await res.json().catch(() => ({}))
    const detail = (body as { detail?: string }).detail
    throw new Error(detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export { apiFetch }
