import { request } from '@/api/client'
import type {
  ActionStatus,
  BinlogFile,
  DatabaseCapabilities,
  DatabaseConfigurationSnapshot,
  DatabaseDiagnostics,
  DatabaseProcess,
  DatabaseSite,
  DatabaseSize,
  ExecutedQuery,
  LockWaitRow,
  PerformanceSection,
  TableSchema,
  TableSize,
  UnsupportedDatabaseDiagnostics,
} from '@/types/database'
import type { TaskPayload } from '@/types/tasks'

export const databaseApi = {
  sites: (): Promise<DatabaseSite[]> => request.get('database/sites').json(),

  schema: (site: string): Promise<TableSchema[]> =>
    request.get('database/schema', { searchParams: { site } }).json(),

  execute: (site: string, query: string, readOnly: boolean): Promise<ExecutedQuery> =>
    request.post('database/queries', { json: { site, query, read_only: readOnly } }).json(),

  diagnostics: (): Promise<DatabaseDiagnostics | UnsupportedDatabaseDiagnostics> =>
    request.get('database/diagnostics').json(),

  processList: (site = ''): Promise<DatabaseProcess[]> =>
    request.get('database/processlist', { searchParams: site ? { site } : {} }).json(),

  lockWaitRows: (site = ''): Promise<LockWaitRow[]> =>
    request.get('database/lockwaits', { searchParams: site ? { site } : {} }).json(),

  size: (site = ''): Promise<DatabaseSize> =>
    request.get('database/size', { searchParams: site ? { site } : {} }).json(),

  tableSizes: (site: string): Promise<TableSize[]> =>
    request.get('database/table-sizes', { searchParams: { site } }).json(),

  performanceReport: (
    reportType: string,
    site = '',
    limit = 20,
    offset = 0,
  ): Promise<PerformanceSection> =>
    request
      .get('database/performance-report', {
        searchParams: { report_type: reportType, site, limit, offset },
      })
      .json(),

  killProcess: (processId: number): Promise<ActionStatus> =>
    request.post('database/processlist/kill', { json: { process_id: processId } }).json(),

  binlogs: {
    list: (): Promise<BinlogFile[]> => request.get('database/binlogs').json(),
    purge: (upTo: string): Promise<ActionStatus> =>
      request.post('database/binlogs/purge', { json: { up_to: upTo } }).json(),
  },

  configurations: {
    list: (): Promise<DatabaseConfigurationSnapshot> =>
      request.get('database/configurations').json(),
    set: (variable: string, value: unknown, idempotencyKey: string): Promise<TaskPayload> =>
      request
        .post(`database/configurations/${encodeURIComponent(variable)}`, {
          json: { value },
          headers: { 'Idempotency-Key': idempotencyKey },
        })
        .json(),
  },

  quickActions: {
    capabilities: (): Promise<DatabaseCapabilities> => request.get('database/quick-actions').json(),
    restart: (idempotencyKey: string): Promise<TaskPayload> =>
      request
        .post('database/quick-actions/restart', {
          headers: { 'Idempotency-Key': idempotencyKey },
        })
        .json(),
    setPerformanceSchema: (enabled: boolean, idempotencyKey: string): Promise<TaskPayload> =>
      request
        .post('database/quick-actions/performance-schema', {
          json: { enabled },
          headers: { 'Idempotency-Key': idempotencyKey },
        })
        .json(),
    setInnoDBBufferPoolSize: (sizeMb: number, idempotencyKey: string): Promise<TaskPayload> =>
      request
        .post('database/quick-actions/innodb-buffer-pool-size', {
          json: { size_mb: sizeMb },
          headers: { 'Idempotency-Key': idempotencyKey },
        })
        .json(),
    setMaxConnections: (maxConnections: number, idempotencyKey: string): Promise<TaskPayload> =>
      request
        .post('database/quick-actions/max-connections', {
          json: { max_connections: maxConnections },
          headers: { 'Idempotency-Key': idempotencyKey },
        })
        .json(),
  },
}
