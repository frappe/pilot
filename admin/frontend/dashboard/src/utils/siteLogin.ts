export type SiteLoginLink = {
  url?: string
  hint?: string
}

export const openSiteLogin = async (
  createLink: () => Promise<SiteLoginLink>,
  { onHint }: { onHint?: (hint: string) => void } = {},
) => {
  const link = await createLink()
  if (typeof link?.url !== 'string') {
    throw new Error('The site login link is invalid.')
  }

  const popup = window.open(link.url, '_blank')
  if (!popup) {
    throw new Error('Allow pop-ups to open the site.')
  }
  try {
    popup.opener = null
  } catch {
    // cross-origin already - nothing to clear
  }
  if (link.hint && onHint) {
    onHint(link.hint)
  }
  return link
}
