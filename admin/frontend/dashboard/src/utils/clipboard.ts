// navigator.clipboard exists only in secure contexts; an admin reached over plain HTTP
// on an IP address falls back to a hidden textarea. It sits beside the anchor so a
// dialog's focus trap does not pull focus away before the copy.
const copyWithTextarea = (text: string, anchor: Element): boolean => {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  anchor.parentElement?.appendChild(textarea)
  textarea.select()
  const isCopied = document.execCommand('copy')
  textarea.remove()
  return isCopied
}

/** Copy text to the clipboard; `anchor` is the clicked element, used by the plain-HTTP fallback. */
export const copyText = async (text: string, anchor: EventTarget | null): Promise<boolean> => {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
    return anchor instanceof Element && copyWithTextarea(text, anchor)
  } catch {
    return false
  }
}
