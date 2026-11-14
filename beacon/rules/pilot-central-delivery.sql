-- title: Pilot cannot send alerts to Central
-- severity: High
-- resource_type: Server
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-central-delivery.md
-- interval: 60
-- pending_for: 300
-- resolve_after: 900
-- Pilot writes one notifications line for each alert it sends to Central. Customers do not hear about a failed one. Pilot retries, so the alert waits 5 minutes and needs 3 failures.
SELECT resource_id, '' AS alert_key, countIf(attributes['delivered'] = '0') AS value,
       concat(toString(value), ' of ', toString(count()), ' alerts did not reach Central in 15 min: ',
              argMaxIf(attributes['error'], ts, attributes['delivered'] = '0')) AS detail
FROM datum.logs
WHERE product = 'pilot' AND service = 'notifications' AND attributes['sink'] = 'central'
  AND ts > now() - INTERVAL 15 MINUTE
GROUP BY resource_id
HAVING value >= 3
