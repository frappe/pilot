export const openExternal = (url?: string) => {
  if (!url) return

  let parsed

  try {
    parsed = new URL(url, window.location.origin)
  } catch {
    return
  }

  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return

  window.open(parsed.href, '_blank', 'noopener')
}
