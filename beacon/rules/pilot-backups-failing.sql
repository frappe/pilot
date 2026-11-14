-- title: Site backups failing on many servers
-- severity: High
-- resource_type: Region
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-backups-failing.md
-- interval: 300
-- pending_for: 1800
-- resolve_after: 3600
-- Pilot tells the owner of one site whose backups keep failing. Here the newest backup of a site failed on 5 or more servers, and 10% of the servers that take backups: a platform cause, such as object storage.
-- It is Medium. On half of the servers it pages, because no site in the region is backed up.
SELECT {region:String} AS resource_id, '' AS alert_key, toFloat64(failing) AS value,
       concat('the newest backup failed on ', toString(failing), ' of ', toString(servers), ' servers, ', toString(sites), ' sites') AS detail,
       if(failing >= 0.5 * servers, 'High', 'Medium') AS severity
FROM (
    SELECT countIf(failed > 0) AS failing, count() AS servers, sum(failed) AS sites
    FROM (
        SELECT resource_id, sumIf(value, labels['status'] = 'failed') AS failed, sum(value) AS taken
        FROM datum.latest_value(metric = 'pilot_site_backups', window = 900)
        GROUP BY resource_id
        HAVING taken > 0
    )
)
WHERE failing >= 5 AND failing >= 0.1 * servers
