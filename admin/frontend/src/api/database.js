import { request } from './client'

export const databaseApi = {
  sites: () => request.get('database/sites').json(),

  schema: (site) =>
    request.get('database/schema', { searchParams: { site } }).json(),

  execute: (site, query, readOnly) =>
    request
      .post('database/queries', { json: { site, query, read_only: readOnly } })
      .json(),

  diagnostics: () => request.get('database/diagnostics').json(),

  processList: () => request.get('database/processlist').json(),

  killProcess: (processId) =>
    request.post('database/processlist/kill', { json: { process_id: processId } }).json(),

  binlogs: {
    list: () => request.get('database/binlogs').json(),
    purge: (upTo) => request.post('database/binlogs/purge', { json: { up_to: upTo } }).json(),
  },
}
