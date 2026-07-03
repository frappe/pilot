import { request } from './client'

export const databaseApi = {
  sites: () => request.get('database/playground/sites').json(),

  schema: (site) =>
    request.get('database/playground/schema', { searchParams: { site } }).json(),

  execute: (site, query, readOnly) =>
    request
      .post('database/playground/execute', { json: { site, query, read_only: readOnly } })
      .json(),
}
