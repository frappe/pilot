-- title: Calls to Central
-- resource_type: Virtual Machine
-- requires_metric: pilot_info
-- section: Central
-- unit: 
-- style: Bar
-- is_key_chart: 0
-- sort_order: 10
-- Each sample counts the calls of the 5 minutes before it. report_pilot_event carries the alerts that Pilot sends to the customer.
SELECT toStartOfInterval(s.ts, toIntervalSecond({step:UInt32})) AS ts,
       concat(s.labels['method'], ', ', s.labels['result']) AS series, sum(s.value) AS value
FROM datum.samples AS s
WHERE s.metric = 'pilot_central_requests_last_5_minutes' AND s.resource_id = {resource_id:String}
    AND s.ts >= {start:DateTime64(3)} AND s.ts < {end:DateTime64(3)}
GROUP BY ts, series
ORDER BY ts, series
