import { request } from "@/api/client";

export const databaseApi = {
  sites: () => request.get("database/sites").json(),

  schema: (site) =>
    request.get("database/schema", { searchParams: { site } }).json(),

  execute: (site, query, readOnly) =>
    request
      .post("database/queries", { json: { site, query, read_only: readOnly } })
      .json(),

  diagnostics: () => request.get("database/diagnostics").json(),

  processList: (site = "") =>
    request
      .get("database/processlist", { searchParams: site ? { site } : {} })
      .json(),

  lockWaitRows: (site = "") =>
    request
      .get("database/lockwaits", { searchParams: site ? { site } : {} })
      .json(),

  size: (site = "") =>
    request.get("database/size", { searchParams: site ? { site } : {} }).json(),

  tableSizes: (site) =>
    request.get("database/table-sizes", { searchParams: { site } }).json(),

  performanceReport: (reportType, site = "", limit = 20, offset = 0) =>
    request
      .get("database/performance-report", {
        searchParams: { report_type: reportType, site, limit, offset },
      })
      .json(),

  killProcess: (processId) =>
    request
      .post("database/processlist/kill", { json: { process_id: processId } })
      .json(),

  binlogs: {
    list: () => request.get("database/binlogs").json(),
    purge: (upTo) =>
      request.post("database/binlogs/purge", { json: { up_to: upTo } }).json(),
  },

  configurations: {
    list: () => request.get("database/configurations").json(),
    set: (variable, value, idempotencyKey) =>
      request
        .post(`database/configurations/${encodeURIComponent(variable)}`, {
          json: { value },
          headers: { "Idempotency-Key": idempotencyKey },
        })
        .json(),
  },

  quickActions: {
    capabilities: () => request.get("database/quick-actions").json(),
    restart: (idempotencyKey) =>
      request
        .post("database/quick-actions/restart", {
          headers: { "Idempotency-Key": idempotencyKey },
        })
        .json(),
    setPerformanceSchema: (enabled, idempotencyKey) =>
      request
        .post("database/quick-actions/performance-schema", {
          json: { enabled },
          headers: { "Idempotency-Key": idempotencyKey },
        })
        .json(),
    setInnoDBBufferPoolSize: (sizeMb, idempotencyKey) =>
      request
        .post("database/quick-actions/innodb-buffer-pool-size", {
          json: { size_mb: sizeMb },
          headers: { "Idempotency-Key": idempotencyKey },
        })
        .json(),
    setMaxConnections: (maxConnections, idempotencyKey) =>
      request
        .post("database/quick-actions/max-connections", {
          json: { max_connections: maxConnections },
          headers: { "Idempotency-Key": idempotencyKey },
        })
        .json(),
  },
};
