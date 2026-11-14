-- title: Pilot silent
-- severity: Medium
-- resource_type: Server
-- interval: 60
-- pending_for: 300
-- resolve_after: 120
-- Pilot sends pilot_sites after each round of uptime pings, once a minute. A server that still sends its machine metrics but has no pilot_sites for 10 minutes has no uptime pings and no Sites SLI.
-- machine-silent covers a server that sends nothing. The query looks back 2 days, the span of samples_recent.
SELECT resource_id, '' AS alert_key, toFloat64(dateDiff('second', last_ts, now())) AS value,
       concat('no pilot_sites for ', toString(round(value / 60)), ' min') AS detail
FROM (
    SELECT resource_id, max(ts) AS last_ts
    FROM datum.samples_recent
    WHERE metric = 'pilot_sites' AND ts > now() - INTERVAL 2 DAY
    GROUP BY resource_id
)
WHERE last_ts < now() - INTERVAL 10 MINUTE
  AND resource_id IN (
      SELECT resource_id
      FROM datum.samples_recent
      WHERE metric = 'system_boot_time_seconds' AND ts > now() - INTERVAL 5 MINUTE
  )
