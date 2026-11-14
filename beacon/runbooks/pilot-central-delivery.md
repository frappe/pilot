# Pilot cannot send alerts to Central

Pilot on a server could not report alerts such as Site unavailable to Central. Central does not tell the customer about these problems.

1. Check the Central alerts and the Central health in Beacon. When many servers alert at once, Central or the network failed.
2. When one server alerts, run `pilot doctor` on it and check the Central enrolment. An expired Pilot credential makes Central refuse each call.
4. When Central works again, the next alerts reach it. Tell the affected customers about missed alerts if the outage was long.
