import { toSentenceCase } from './format.ts'

export type SiteAppChoice = {
  name: string
  title?: string
  repo?: string
  stars?: number | null
}

export const isFrappeApp = (app: { repo?: string }) => {
  return Boolean(app.repo?.includes('github.com/frappe/'))
}

// The whole catalog is too much to scan while naming a site: Frappe's own apps
// plus whatever is already cloned onto this bench, marketplace for the rest.
export const buildSiteAppChoices = (
  registry: SiteAppChoice[] = [],
  benchApps: SiteAppChoice[] = [],
) => {
  const apps = new Map<string, SiteAppChoice>()
  registry
    .filter((app) => app.name !== 'frappe' && isFrappeApp(app))
    .forEach((app) => apps.set(app.name, app))
  benchApps
    // Bench-only apps carry no registry title, so this falls back to the folder
    // name - `insights` next to `ERPNext`. Same tidy-up the marketplace does.
    .filter((app) => app.name !== 'frappe' && !apps.has(app.name))
    .forEach((app) =>
      apps.set(app.name, { name: app.name, title: toSentenceCase(app.title || app.name) }),
    )
  // Stars float the flagship apps without a hand-kept list going stale;
  // bench-only apps have none, so they settle alphabetically at the end.
  return [...apps.values()].sort(
    (a, b) =>
      (b.stars ?? -1) - (a.stars ?? -1) || (a.title || a.name).localeCompare(b.title || b.name),
  )
}
