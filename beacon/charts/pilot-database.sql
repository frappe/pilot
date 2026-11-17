-- title: Database
-- resource_type: Virtual Machine
-- requires_metric: pilot_info
-- section: Database
-- unit:
-- style: Line
-- is_key_chart: 0
-- sort_order: 10
-- 1 when the database of the server accepts connections, 0 when it does not. A bucket with one failed check shows 0.
SELECT toStartOfInterval(s.ts, toIntervalSecond({step:UInt32})) AS ts, 'Up' AS series, min(s.value) AS value
FROM datum.samples AS s
WHERE s.metric = 'pilot_database_up' AND s.resource_id = {resource_id:String}
    AND s.ts >= {start:DateTime64(3)} AND s.ts < {end:DateTime64(3)}
GROUP BY ts, series
ORDER BY ts, series
