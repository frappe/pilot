import { request } from './client'

export const databaseApi = {
  sites: () => request.get('database/sites').json(),

  schema: (site) =>
    request.get('database/schema', { searchParams: { site } }).json(),

  execute: (site, query, readOnly) =>
    request
      .post('database/queries', { json: { site, query, read_only: readOnly } })
      .json(),
}
