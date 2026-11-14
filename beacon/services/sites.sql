-- title: Sites
-- description: Sites answer requests on the servers that run them.
-- scope: Region
-- slo_target: 99.9
-- sort_order: 1
-- Pilot pings /api/method/ping on each site once a minute and sends pilot_sites by state. App request hooks run on the ping, so an app fault can count as bad.
-- A sample more than 1 minute in the future comes from a wrong clock, and must not stay the newest value.
SELECT resource_id,
       toInt64(argMaxIf(value, ts, labels['state'] = 'up')) AS good,
       toInt64(good + argMaxIf(value, ts, labels['state'] = 'down')) AS valid,
       max(ts) AS last_ts
FROM datum.samples_recent
WHERE metric = 'pilot_sites' AND ts > now() - INTERVAL 30 MINUTE AND ts <= now() + INTERVAL 1 MINUTE
GROUP BY resource_id
