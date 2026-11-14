-- title: Pilot silent
-- severity: Medium
-- resource_type: Server
-- interval: 300
-- pending_for: 300
-- resolve_after: 300
-- Pilot sends pilot_sites after each round of uptime pings, once a minute. A server that sent it in the last day and stopped for 10 minutes has no uptime pings and no Sites SLI.
-- The lookback is 1 day, because Atlas lists a stopped VM as Active and its Pilot is silent on purpose. A Pilot silent for more than 1 day resolves; the Sites SLI coverage still shows it.
SELECT resource_id, '' AS alert_key, toFloat64(dateDiff('second', max(ts), now())) AS value,
       concat('no pilot_sites for ', toString(round(value / 60)), ' min') AS detail
FROM datum.samples_recent
WHERE metric = 'pilot_sites' AND ts > now() - INTERVAL 1 DAY
GROUP BY resource_id
HAVING value >= 600
