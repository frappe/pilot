export function safeRedirect(value, fallback = '/') {
  return typeof value === 'string' &&
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.startsWith('/login')
    ? value
    : fallback
}

// Off-SPA targets (the code editor) have no route here and need a full load.
export function redirectAfterLogin(router, value) {
  const target = safeRedirect(value)
  if (router.resolve(target).matched.length) {
    router.replace(target)
  } else {
    window.location.assign(target)
  }
}
