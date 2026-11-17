-- title: Sites Down
-- resource_type: Virtual Machine
-- requires_metric: pilot_info
-- section: Sites
-- unit: 
-- style: Line
-- is_key_chart: 1
-- sort_order: 10
-- Down and app_error together, as the pilot-sites-down rule counts them.
SELECT toStartOfInterval(sample_ts, toIntervalSecond({step:UInt32})) AS ts, 'Down' AS series, max(down) AS value
FROM (
    SELECT s.ts AS sample_ts, sumIf(s.value, s.labels['state'] IN ('down', 'app_error')) AS down
    FROM datum.samples AS s
    WHERE s.metric = 'pilot_sites' AND s.resource_id = {resource_id:String}
        AND s.ts >= {start:DateTime64(3)} AND s.ts < {end:DateTime64(3)}
    GROUP BY sample_ts
)
GROUP BY ts, series
ORDER BY ts, series
