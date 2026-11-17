-- title: Many sites down
-- description: Many sites on this server stopped answering. The server, not one site, is likely at fault.
-- severity: High
-- resource_type: Virtual Machine
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-sites-down.md
-- interval: 60
-- pending_for: 300
-- resolve_after: 300
-- One site that is down is the customer's problem, and Pilot tells the customer. Many sites down on one server at once is the server: its bench, nginx or database. 3 sites, and 20% of the checked sites, page.
-- An app_error site counts here too. Many app errors on one server at once come from a shared app, such as after a bench update.
-- pilot_database_up only explains the alert: a database that is down shows in the sites. Pilot sends it each 5 minutes, so the query reads it over 10 minutes.
SELECT resource_id, '' AS alert_key, down AS value,
       concat(toString(down), ' of ', toString(up + down), ' sites down (limit 3 sites and 20%)',
              multiIf(database_samples = 0, '', database_up = 1, ', database accepts connections', ', database does not accept connections')) AS detail
FROM (
    SELECT resource_id,
           argMaxIf(value, ts, metric = 'pilot_sites' AND labels['state'] = 'down' AND ts > now() - INTERVAL 5 MINUTE)
             + argMaxIf(value, ts, metric = 'pilot_sites' AND labels['state'] = 'app_error' AND ts > now() - INTERVAL 5 MINUTE) AS down,
           argMaxIf(value, ts, metric = 'pilot_sites' AND labels['state'] = 'up' AND ts > now() - INTERVAL 5 MINUTE) AS up,
           argMaxIf(value, ts, metric = 'pilot_database_up') AS database_up,
           countIf(metric = 'pilot_database_up') AS database_samples
    FROM datum.samples_recent
    WHERE metric IN ('pilot_sites', 'pilot_database_up') AND ts > now() - INTERVAL 10 MINUTE AND ts <= now() + INTERVAL 1 MINUTE
    GROUP BY resource_id
)
WHERE down >= 3 AND down >= 0.2 * (up + down)
