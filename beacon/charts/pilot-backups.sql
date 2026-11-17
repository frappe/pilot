-- title: Newest Backups
-- resource_type: Virtual Machine
-- requires_metric: pilot_info
-- section: Backups
-- unit: 
-- style: Bar
-- is_key_chart: 0
-- sort_order: 10
-- The sites by the status of their newest backup, as the pilot-backups-failing rule reads it.
SELECT toStartOfInterval(s.ts, toIntervalSecond({step:UInt32})) AS ts, initcap(s.labels['status']) AS series, avg(s.value) AS value
FROM datum.samples AS s
WHERE s.metric = 'pilot_site_backups' AND s.resource_id = {resource_id:String}
    AND s.ts >= {start:DateTime64(3)} AND s.ts < {end:DateTime64(3)}
GROUP BY ts, series
ORDER BY ts, series
