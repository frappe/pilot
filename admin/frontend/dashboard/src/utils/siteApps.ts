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

export const getCommitUrl = (repo = '', commit = '') => {
  const path = /github\.com[/:]([^/]+\/[^/]+?)(?:\.git)?\/?$/.exec(repo)?.[1]

  return path && commit ? `https://github.com/${path}/commit/${commit}` : ''
}

export const buildSiteAppChoices = (
  registry: SiteAppChoice[] = [],
  benchApps: SiteAppChoice[] = [],
) => {
  const apps = new Map<string, SiteAppChoice>()
  registry.filter((app) => app.name !== 'frappe').forEach((app) => apps.set(app.name, app))
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

export type SiteTemplateChoice = {
  name: string
  apps: string[]
}

/** A template's apps, split by whether the new-site dialog offers them. */
export const resolveTemplateApps = (template: SiteTemplateChoice, choices: SiteAppChoice[]) => {
  const offered = new Set(choices.map((app) => app.name))
  const apps = template.apps.filter((app) => app !== 'frappe')
  return {
    apps: apps.filter((app) => offered.has(app)),
    missing: apps.filter((app) => !offered.has(app)),
  }
}

/** The template whose offered apps are exactly the selection, in any order. */
export const findMatchingTemplate = <T extends SiteTemplateChoice>(
  templates: T[],
  choices: SiteAppChoice[],
  selected: string[],
) => {
  const wanted = new Set(selected)
  return templates.find((template) => {
    const { apps } = resolveTemplateApps(template, choices)
    return apps.length > 0 && apps.length === wanted.size && apps.every((app) => wanted.has(app))
  })
}
