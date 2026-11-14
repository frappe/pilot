-- title: Pilot silent
-- severity: Medium
-- resource_type: Server
-- interval: 60
-- pending_for: 300
-- resolve_after: 120
-- Pilot sends pilot_info each 5 minutes from its telemetry thread, and pilot_sites after each round of uptime pings. When one stops for 10 minutes, Pilot or one of its threads stopped, and the Sites SLI has no data for the server.
-- Only a VM that Atlas expects to run counts, so a stopped or sleeping VM does not alert. The query looks back 2 days, the span of samples_recent.
SELECT resource_id, '' AS alert_key, toFloat64(dateDiff('second', least(info_ts, sites_ts), now())) AS value,
       concat('no ', if(info_ts < sites_ts, 'pilot_info', 'pilot_sites'), ' for ', toString(round(value / 60)), ' min') AS detail
FROM (
    SELECT resource_id, maxIf(ts, metric = 'pilot_info') AS info_ts, maxIf(ts, metric = 'pilot_sites') AS sites_ts
    FROM datum.samples_recent
    WHERE metric IN ('pilot_info', 'pilot_sites') AND ts > now() - INTERVAL 2 DAY
    GROUP BY resource_id
)
WHERE least(info_ts, sites_ts) < now() - INTERVAL 10 MINUTE
  AND resource_id IN (
      SELECT labels['machine']
      FROM datum.samples_recent
      WHERE metric = 'atlas_machine' AND resource_id = 'atlas' AND ts > now() - INTERVAL 5 MINUTE
  )
