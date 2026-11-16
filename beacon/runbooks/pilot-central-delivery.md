# Pilot cannot send alerts to Central

Pilot on a server could not report alerts such as Site unavailable to Central. Central does not tell the customer about these problems.

1. Check the Central alerts and the Central health in Beacon. When many servers alert at once, Central or the network failed.
2. When one server alerts, run `pilot doctor` on it and check the Central enrolment. An expired Pilot credential makes Central refuse each call with status 401.
3. Status 403 on `report_pilot_event` in the Central access log means that Central does not accept the event type, for example from a Pilot build that sends an event Central does not define. Update Pilot on the server, or add the event type to Central.
4. When Central works again, the next alerts reach it. Tell the affected customers about missed alerts if the outage was long.
