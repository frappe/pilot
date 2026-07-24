export const PILLS = [
  'All',
  'Integrations',
  'Utility',
  'Payments',
  'Localization',
  'Business',
  'Dev Tools',
]

const PILL_BY_CATEGORY = {
  Integrations: 'Integrations',
  'CRM Integration': 'Integrations',
  Communication: 'Integrations',
  'E-Commerce': 'Integrations',
  Mobile: 'Integrations',
  Utility: 'Utility',
  Utilities: 'Utility',
  Extension: 'Utility',
  Extensions: 'Utility',
  Productivity: 'Utility',
  Storage: 'Utility',
  Files: 'Utility',
  Collaboration: 'Utility',
  AI: 'Utility',
  Themes: 'Utility',
  Payments: 'Payments',
  Localization: 'Localization',
  Compliance: 'Localization',
  Business: 'Business',
  Accounting: 'Business',
  'Human Resources': 'Business',
  'Customer Relations': 'Business',
  Healthcare: 'Business',
  Retail: 'Business',
  'Non-Profit': 'Business',
  Analytics: 'Business',
  Marketing: 'Business',
  Support: 'Business',
  Developer: 'Dev Tools',
  'Developer Tools': 'Dev Tools',
  Website: 'Dev Tools',
}

// 'Featured' is a meta-tag; unknown values default to Utility.
function pillsFor(app) {
  const categories = app.categories?.length ? app.categories : app.category ? [app.category] : []
  const mapped = categories
    .filter((category) => category !== 'Featured')
    .map((category) => PILL_BY_CATEGORY[category] || 'Utility')
  return new Set(mapped)
}

export function matchesPill(app, pill) {
  return pill === 'All' || pillsFor(app).has(pill)
}
