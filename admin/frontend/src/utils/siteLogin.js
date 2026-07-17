export async function openSiteLogin(createLink) {
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
    // cross-origin already — nothing to clear
  }
  return link
}
