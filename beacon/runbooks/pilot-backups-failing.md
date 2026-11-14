# Site backups failing on many servers

On 5 or more servers in the region, and on 10% of the servers that take backups, the newest backup of half or more of the sites failed (Medium). On half of the servers it pages (High). One server or one site alone is the problem of its owner, and Pilot already tells them.

1. On one affected server, read the newest `"event": "backup"` entry with `"status": "failed"` in `<bench>/logs/audit_<year>_<week>.jsonl`. The Pilot Admin UI also shows the backups of each site.
2. When the upload to S3 fails on many servers, check the object storage of the region and its Beacon alerts.
3. When the disk is full, free space or grow the disk. A backup needs free space for the database dump.
4. After the fix, the next scheduled backup runs within 1 hour. The alert resolves when the newest backups succeed.
