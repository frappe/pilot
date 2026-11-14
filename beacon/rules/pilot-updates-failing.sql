-- title: App update failing on many servers
-- severity: Low
-- resource_type: Region
-- runbook_url: https://github.com/frappe/pilot/blob/develop/beacon/runbooks/pilot-updates-failing.md
-- interval: 300
-- pending_for: 300
-- resolve_after: 3600
-- Pilot tells the owner of one server whose update failed. The same release of one repository that fails on 3 or more servers points to the release, not to the servers.
SELECT {region:String} AS resource_id, concat(repository, '@', to_version) AS alert_key, toFloat64(servers) AS value,
       concat(repository, ' ', to_version, ' failed on ', toString(servers), ' servers in 6 h, last in ', last_phase, ': ', last_message) AS detail
FROM (
    SELECT attributes['repository'] AS repository, attributes['to_version'] AS to_version,
           uniqExact(resource_id) AS servers, argMax(attributes['phase'], ts) AS last_phase,
           substring(argMax(message, ts), 1, 200) AS last_message
    FROM datum.logs
    WHERE product = 'pilot' AND service = 'update' AND source = 'event' AND attributes['status'] = 'failed'
      AND ts > now() - INTERVAL 6 HOUR AND resource_id NOT IN {excluded:Array(String)}
    GROUP BY repository, to_version
)
WHERE servers >= 3
