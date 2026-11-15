-- title: Sites
-- description: Sites answer requests on the servers that run them.
-- scope: Region
-- slo_target: 99.9
-- sort_order: 1
-- show_on_status_page: 0
-- Hidden from the status page: a fault of the customer's own app counts as down, and the platform does not control it. Beacon still measures it.
-- Pilot pings /api/method/ping on each site once a minute and sends pilot_sites by state. A ping that app code failed is app_error, and the SLI leaves it out: only up and down count.
-- A sample more than 1 minute in the future comes from a wrong clock, and must not stay the newest value.
SELECT resource_id,
       toInt64(argMaxIf(value, ts, labels['state'] = 'up')) AS good,
       toInt64(good + argMaxIf(value, ts, labels['state'] = 'down')) AS valid,
       max(ts) AS last_ts
FROM datum.samples_recent
WHERE metric = 'pilot_sites' AND ts > now() - INTERVAL 30 MINUTE AND ts <= now() + INTERVAL 1 MINUTE
GROUP BY resource_id
