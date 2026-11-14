-- title: Pilot cannot send alerts to Central
-- severity: High
-- resource_type: Server
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-central-delivery.md
-- interval: 60
-- pending_for: 300
-- resolve_after: 900
-- Pilot counts its calls to Central each 5 minutes. report_pilot_event carries the alerts that Pilot sends to the customer, and the customer does not hear about a failed one. Pilot retries, so the alert waits 5 minutes and needs 3 failures.
SELECT resource_id, '' AS alert_key, failed AS value,
       concat(toString(failed), ' of ', toString(total), ' alerts did not reach Central in 15 min') AS detail
FROM (
    SELECT resource_id, sumIf(value, labels['result'] = 'failed') AS failed, sum(value) AS total
    FROM datum.samples_recent
    WHERE metric = 'pilot_central_requests_last_5_minutes' AND labels['method'] = 'report_pilot_event'
      AND ts > now() - INTERVAL 15 MINUTE
    GROUP BY resource_id
)
WHERE failed >= 3
