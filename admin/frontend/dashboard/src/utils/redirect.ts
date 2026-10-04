export const safeRedirect = (value: unknown, fallback = '/') => {
  return typeof value === 'string' &&
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.startsWith('/login')
    ? value
    : fallback
}
