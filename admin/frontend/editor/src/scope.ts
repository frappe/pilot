// The page path picks what the editor opens: /editor/<app> or /editor/site/<site>, behind an
// optional mount such as a site's /_pilot. ?base= opens a folder inside it.
const match = location.pathname.match(/^(.*?)\/editor\/(?:(site)\/)?([^/]+)\/?$/)
const name = decodeURIComponent(match?.[3] || '')

export const MOUNT = match?.[1] || ''
export const SITE = match?.[2] ? name : ''
export const APP = SITE ? '' : name
// A site folder has no git, so the site editor hides source control and blame.
export const HAS_GIT = !SITE
export const BASE = new URLSearchParams(location.search).get('base') || ''

export const API_PREFIX = SITE
  ? `${MOUNT}/api/v1/sites/${encodeURIComponent(SITE)}/editor`
  : `${MOUNT}/api/v1/editor`

export const SEARCH_PARAMS: Record<string, string> = {
  ...(APP && { app: APP }),
  ...(BASE && { base: BASE }),
}

if (name) document.title = `${name} · Editor`
