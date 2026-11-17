-- title: Sites by State
-- resource_type: Virtual Machine
-- requires_metric: pilot_info
-- section: Sites
-- unit:
-- style: Bar
-- is_key_chart: 0
-- sort_order: 20
-- The state of each site in the uptime pings. An app_error is a failed ping that app code caused. Empty states are left out, to stay under the row limit.
SELECT toStartOfInterval(s.ts, toIntervalSecond({step:UInt32})) AS ts,
       replaceAll(initcap(s.labels['state']), '_', ' ') AS series, avg(s.value) AS value
FROM datum.samples AS s
WHERE s.metric = 'pilot_sites' AND s.resource_id = {resource_id:String}
    AND s.ts >= {start:DateTime64(3)} AND s.ts < {end:DateTime64(3)}
GROUP BY ts, series
HAVING value > 0
ORDER BY ts, series
