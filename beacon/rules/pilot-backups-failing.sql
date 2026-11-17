-- title: Backups failing on many servers
-- description: The newest backup failed for most sites on many servers. Restores may lose recent data.
-- severity: High
-- resource_type: Region
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-backups-failing.md
-- interval: 300
-- pending_for: 1800
-- resolve_after: 3600
-- Pilot tells the owner of one site whose backups keep failing. A server fails here when the newest backup of half or more of its sites failed, so one broken site does not count.
-- 5 or more failing servers that are 10% of the servers that take backups point to a platform cause, such as object storage.
-- It is Medium. On half of the servers it pages, because half of the region has no new backup.
-- affected names each failing server with up to 10 of its failed sites.
SELECT {region:String} AS resource_id, '' AS alert_key, toFloat64(failing) AS value,
       concat('newest backup failed for most sites on ', toString(failing), ' of ', toString(servers), ' servers (limit 5 servers and 10%), ', toString(sites), ' sites') AS detail,
       if(failing >= 0.5 * servers, 'High', 'Medium') AS severity,
       affected
FROM (
    SELECT countIf(is_failing) AS failing, count() AS servers, sum(failed) AS sites,
           mapFromArrays(groupArrayIf(resource_id, is_failing), groupArrayIf(line, is_failing)) AS affected
    FROM (
        SELECT b.resource_id AS resource_id, b.failed AS failed, failed > 0 AND failed >= 0.5 * taken AS is_failing,
               concat(toString(failed), ' of ', toString(taken), if(taken = 1, ' site', ' sites'), ' failed', if(empty(f.sites), '', concat(': ', arrayStringConcat(arraySlice(arraySort(f.sites), 1, 10), ', ')))) AS line
        FROM (
            SELECT resource_id, sumIf(value, labels['status'] = 'failed') AS failed, sum(value) AS taken
            FROM datum.latest_value(metric = 'pilot_site_backups', window = 900)
            WHERE resource_id NOT IN {excluded:Array(String)}
            GROUP BY resource_id
            HAVING taken > 0
        ) AS b
        LEFT JOIN (
            SELECT resource_id, groupArray(labels['site']) AS sites
            FROM datum.latest_value(metric = 'pilot_site_backup_failed', window = 900)
            GROUP BY resource_id
        ) AS f ON f.resource_id = b.resource_id
    )
)
WHERE failing >= 5 AND failing >= 0.1 * servers
