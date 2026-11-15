# App update failing on many servers

The update to one release of one app repository failed on 3 or more servers in the region in 6 hours. The alert key is `<repository>@<version>`, such as `frappe/erpnext@v16.2.0`. Pilot tells the owner of each server, and the server keeps its old version or waits for a person in the Pilot Admin UI.

1. Read the phase and the message in the alert detail. A failure in `migrate` usually is a patch of the release. A failure in `build` usually is the assets or a dependency of the release.
2. Find the update lines in the logs of the region: `product = 'pilot'`, `service = 'update'`, `attributes['repository']` and `attributes['to_version']` of the alert. Compare the messages of the servers. The same error on each server points to the release.
3. Tell the owner of the app repository, with the error and the number of servers.
4. When the error differs on each server, check each server. The alert can be a coincidence.
5. The alert resolves 1 hour after the last failure. A fixed release has a new version, so it does not keep this alert open.
