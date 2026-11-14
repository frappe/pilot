# Pilot cannot send alerts to Central

Pilot on a server could not report alerts such as Site unavailable to Central. Central does not tell the customer about these problems.

1. Read the error in the alert detail. A 401 means the Pilot credential of the server expired. A timeout or 5xx means Central or the network failed.
2. Check the Central alerts and the Central health in Beacon.
3. On the server, run `pilot doctor` and check the Central enrolment.
4. When Central works again, the next alerts reach it. Tell the affected customers about missed alerts if the outage was long.
