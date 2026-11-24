# Pilot stopped sending metrics

Pilot on a server sent no `pilot_info` or no `pilot_sites` for 10 minutes. Beacon does not watch the sites on this server until Pilot sends again.

1. Check the server in Beacon. When the VM or its host is down, follow that incident instead.
2. When many servers alert at once, check the Datum ingest of the region and its telemetry endpoint.
3. On the server, run `pilot doctor` and check that the Pilot service runs. The detail names the metric that stopped: `pilot_info` comes from the telemetry thread and `pilot_sites` from the uptime thread.
4. Restart Pilot when a thread stopped, and keep its log for the bug report. Check that the sites answer.
