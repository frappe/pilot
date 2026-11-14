# Sites down on server

Pilot pings each site on the server once a minute, and 3 or more sites, and 20% or more of them, do not answer. The fault is on the server, not in one site.

1. Open a shell on the server. Run `pilot ls` to see the state of each bench.
2. Check the bench processes, nginx and MariaDB with `systemctl status` for the units of the bench.
3. Read the bench logs in `benches/<bench>/logs/` for the time the sites went down.
4. Restart the bench with `pilot -b <bench> restart`. If MariaDB does not start, check its disk space first.
5. Post an update on the incident when customer sites stay down.
