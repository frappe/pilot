# Writing a domain provider

A **domain provider** is an optional external program that takes over custom-domain
routing for a bench — for managed hosting where an edge proxy / load balancer / DNS
control plane sits in front of the bench.

- bench-cli looks for a binary named **`bench-domain-provider`** on `PATH`.
- **Not found** → bench-cli uses its built-in behaviour (DNS verification + writing into
  the site's `site_config.json`).
- **Found** → the provider takes over. bench-cli calls it and reads only its **exit code**
  and **stdout**.

Implement it in any language. The contract in code:
[`bench_cli/core/domain_controller.py`](../bench_cli/core/domain_controller.py)
(`DomainRouteProvider`).

## Install

- Build one executable named exactly `bench-domain-provider`.
- Put it on `PATH` for the user running bench-cli and the admin service.
- Make it executable.

```sh
install -m 0755 ./my-domain-provider /usr/local/bin/bench-domain-provider
```

Any config your binary needs (API URL, credentials) is your own concern — read it from a
file or env you control.

## Verbs

```
bench-domain-provider generate-dns-records <site> <domain>
bench-domain-provider register             <domain>
bench-domain-provider deregister           <domain>
bench-domain-provider wildcard-domains
```

Only `generate-dns-records` takes the **site** (its first positional argument, for the
CNAME target); the rest operate on the domain alone. The process inherits the caller's
environment; bench-cli sets no special variables. Any config your binary needs (API URL,
credentials) is your own concern — read it from a file or env you control.

## `generate-dns-records <site> <domain>` — pre-flight

Validate the domain and tell the user which DNS records to set. **Read-only:** never touch
the proxy/DNS here — only report records. The actual provisioning happens in `register`.

- **stdout:** a JSON object with two record sets — `cname` and `a`, one per validation
  method — or blank/`{}` if no records are needed. Either set may be empty; the UI shows
  each non-empty set as an option, listing every record in it:
  ```json
  {
    "cname": [{ "type": "CNAME", "host": "app.example.com", "value": "site.bench.example.com" }],
    "a":     [{ "type": "A",     "host": "app.example.com", "value": "203.0.113.10" }]
  }
  ```
- A set is the **complete recipe** for that path, so add extra records to it when you need
  more than the route record — e.g. a `TXT` ownership/key-validation record the user must
  set alongside the CNAME or A:
  ```json
  {
    "cname": [
      { "type": "CNAME", "host": "app.example.com",      "value": "site.bench.example.com" },
      { "type": "TXT",   "host": "_bench.app.example.com", "value": "bench-verify=abc123" }
    ]
  }
  ```
- **Return blank/`{}`** when the user needs to do nothing — a subdomain under a wildcard you
  already route, or a domain you provision **fully automatically** in `register` (proxy +
  DNS, no manual step).
- **Exit:** `0` to proceed, non-zero to abort (stderr shown).
- Prefer to **fail open** on an outage here — the real gate is `register`.

## `register <domain>` — claim **and provision** the route

Make `<domain>` actually route to this bench: claim the name and configure the edge proxy
/ load balancer / DNS / cert so traffic reaches the bench. Called **before** the local
site is created, so a failure leaves no orphan.

- **stdout:** ignored.
- **Exit `0`:** route is live. bench-cli then writes the domain locally and re-runs its own
  `setup-nginx` / `setup-letsencrypt` (your proxy sits in front — both layers apply).
- **Exit non-zero:** bench-cli aborts and shows stderr. Undo any partial proxy/DNS changes
  first.
- Must be **idempotent** — a retry should re-apply, not duplicate or error.
- **Fail closed** on an unreachable control plane: if the route wasn't provisioned, don't
  let the site get created.

## `deregister <domain>` — tear down / rollback

Inverse of `register`: remove the proxy/DNS route and release the name. Called after a
site is dropped, and as the rollback when a create fails midway.

- **stdout:** ignored.
- **Always exit `0`** — best-effort. bench-cli removes the domain locally regardless; a
  missed teardown just leaves a stale route to clean up later. A non-zero here would throw
  an error on an otherwise-successful drop.

## `wildcard-domains` — host-level query

The wildcard patterns this host may create subdomains under. bench-cli uses them to
constrain site names and to suggest subdomains in the UI.

- **stdout:** a JSON array of patterns, or blank for none:
  ```json
  ["*.region.example.com", "*.eu.example.com"]
  ```
- **Fail soft** — return blank (exit `0`) on an outage. Non-zero raises an error and breaks
  the Add-Domain UI.

## Errors

One mechanism: **exit non-zero, write the message to stderr.** That text becomes the error
shown to the user; stdout is ignored on failure.

```sh
echo "subdomain 'app' is already taken — choose another" >&2
exit 1
```

bench-cli only checks zero vs non-zero. A useful convention for your own code:

| Code | Meaning                                              |
|------|------------------------------------------------------|
| `0`  | Success (or a deliberate fail-soft/fail-open no-op)  |
| `1`  | Transport/config failure (unreachable, no creds)     |
| `2`  | Declined (taken / reserved / at-limit / invalid)     |
| `64` | Usage error — unknown verb / wrong arg count         |

## Skeleton

```python
#!/usr/bin/env python3
import json, sys

def main(argv):
    verb = argv[1] if len(argv) > 1 else ""

    if verb == "generate-dns-records" and len(argv) == 4:
        site, domain = argv[2], argv[3]
        return 0  # blank = no records; or print {"cname": [...], "a": [...]}

    if verb == "register" and len(argv) == 3:
        domain = argv[2]
        # provision the route; on conflict:
        #   print(f"{domain} is already taken", file=sys.stderr); return 2
        return 0

    if verb == "deregister" and len(argv) == 3:
        return 0  # best-effort; never block a drop

    if verb == "wildcard-domains":
        print(json.dumps(["*.region.example.com"]))  # or nothing for none
        return 0

    print("usage: bench-domain-provider generate-dns-records <site> <domain> | "
          "register <domain> | deregister <domain> | wildcard-domains", file=sys.stderr)
    return 64

if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

## Test

```sh
./bench-domain-provider generate-dns-records mysite app.example.com; echo "exit=$?"

./bench-domain-provider wildcard-domains; echo "exit=$?"

# declined path: expect non-zero + stderr message
./bench-domain-provider register taken.example.com; echo "exit=$?"
```

Then install it on `PATH` and exercise the real flows: `bench new-site` with a
wildcard-matching name, and Add/Remove Domain in the admin UI.
