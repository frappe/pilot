-- title: App update failing on many servers
-- description: The same app release failed to update on 3 or more servers. The release is likely broken.
-- severity: Low
-- resource_type: Region
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-updates-failing.md
-- interval: 300
-- pending_for: 300
-- resolve_after: 3600
-- Pilot tells the owner of one server whose update failed. The same release of one repository that fails on 3 or more servers points to the release, not to the servers.
-- affected names each server with its site, if any, and the phase and message of its last failure.
SELECT {region:String} AS resource_id, concat(repository, '@', to_version) AS alert_key, toFloat64(servers) AS value,
       concat(repository, ' ', to_version, ' failed on ', toString(servers), ' servers in 6 h (limit 3), last in ', last_phase, ': ', last_message) AS detail,
       affected
FROM (
    SELECT repository, to_version, count() AS servers, argMax(phase, last_ts) AS last_phase, argMax(message, last_ts) AS last_message,
           mapFromArrays(groupArray(50)(resource_id), groupArray(50)(concat(if(site = '', '', concat(site, ', ')), phase, ': ', replaceRegexpAll(message, '\\s+', ' ')))) AS affected
    FROM (
        SELECT resource_id, attributes['repository'] AS repository, attributes['to_version'] AS to_version, max(ts) AS last_ts,
               argMax(attributes['site'], ts) AS site, argMax(attributes['phase'], ts) AS phase,
               substring(argMax(message, ts), 1, 200) AS message
        FROM datum.logs
        WHERE product = 'pilot' AND service = 'update' AND source = 'event' AND attributes['status'] = 'failed'
          AND ts > now() - INTERVAL 6 HOUR AND resource_id NOT IN {excluded:Array(String)}
        GROUP BY resource_id, repository, to_version
    )
    GROUP BY repository, to_version
)
WHERE servers >= 3
