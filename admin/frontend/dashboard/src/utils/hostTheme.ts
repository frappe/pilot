// A site embeds the code editor, and so this sign-in page, in an iframe on its own origin.
// Follow the host page's theme there: Desk sets data-theme, frappe-ui apps the dark class.
export const followHostTheme = () => {
  let host: HTMLElement
  try {
    if (window.parent === window) return
    host = window.parent.document.documentElement
  } catch {
    return
  }
  const apply = () => {
    const isDark = host.getAttribute('data-theme') === 'dark' || host.classList.contains('dark')
    document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light')
  }
  apply()
  new MutationObserver(apply).observe(host, { attributes: true, attributeFilter: ['data-theme', 'class'] })
}
