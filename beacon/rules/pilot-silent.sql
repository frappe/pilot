-- title: Pilot silent
-- severity: Medium
-- resource_type: Server
-- interval: 60
-- pending_for: 300
-- resolve_after: 120
-- Pilot sends pilot_sites after each round of uptime pings, once a minute. A server that sent any telemetry in the last 14 days and has no pilot_sites for 10 minutes has no uptime pings and no Sites SLI.
-- Atlas lists a sleeping or stopped VM as Suspended, so Beacon drops its row. Hosts, service nodes, Atlas and Cargo also send telemetry, so the query leaves out the IDs that Atlas reports for them.
SELECT r.resource_id, '' AS alert_key, toFloat64(if(l.last_ts IS NULL, 3600, dateDiff('second', l.last_ts, now()))) AS value,
       if(l.last_ts IS NULL, 'no pilot_sites for over 1 h', concat('no pilot_sites for ', toString(round(value / 60)), ' min')) AS detail
FROM (
    SELECT DISTINCT resource_id
    FROM datum.daily_ingestion_stats
    WHERE date > today() - 14 AND NOT startsWith(resource_id, 'atlas') AND NOT startsWith(resource_id, 'cargo')
      AND resource_id NOT IN (
          SELECT arrayJoin([labels['server'], labels['node']])
          FROM datum.samples_recent
          WHERE metric IN ('atlas_metal_server_info', 'atlas_service_node') AND ts > now() - INTERVAL 1 HOUR
      )
) AS r
LEFT JOIN (
    SELECT resource_id, max(ts) AS last_ts
    FROM datum.samples_recent
    WHERE metric = 'pilot_sites' AND ts > now() - INTERVAL 1 HOUR
    GROUP BY resource_id
) AS l ON l.resource_id = r.resource_id
WHERE (l.last_ts IS NULL OR l.last_ts < now() - INTERVAL 10 MINUTE)
  -- Without the Atlas inventory the query cannot tell a host from a server. atlas-silent covers that case.
  AND (SELECT count() FROM datum.samples_recent WHERE metric = 'atlas_metal_server_info' AND ts > now() - INTERVAL 10 MINUTE) > 0
SETTINGS join_use_nulls = 1
