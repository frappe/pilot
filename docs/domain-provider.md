# Domain Provider

A domain provider is an optional executable that takes over custom-domain routing for managed hosting, edge proxies, load balancers, or DNS control planes.

Pilot looks for `bench-domain-provider` on `PATH`. Any deployment can provide that binary to replace Pilot's naive local DNS/domain behavior without changing Pilot code.

- Not found: Pilot uses built-in DNS verification and writes local site config.
- Found: Pilot calls the executable and trusts its exit code and stdout.

The implementation contract lives in `pilot/core/adapters/domain_provider.py`.

## Install

Build one executable named exactly `bench-domain-provider`, make it executable, and put it on `PATH` for the user running bench and the Admin service.

```sh
install -m 0755 ./my-domain-provider /usr/local/bin/bench-domain-provider
```

Provider config such as API URLs and credentials belongs to your binary. Read it from your own files or environment.

## Verbs

```text
bench-domain-provider generate-dns-records <site> <domain>
bench-domain-provider register             <domain>
bench-domain-provider deregister           <domain>
bench-domain-provider wildcard-domains
bench-domain-provider proxy-servers
```

Only `generate-dns-records` receives the site name. The process inherits the caller environment; Pilot sets no provider-specific variables.

## `generate-dns-records`

This is a read-only preflight. Validate the domain and print DNS records the user can set. Do not provision proxy or DNS state here.

Stdout is a JSON object with `cname` and `a` record sets, or blank/`{}` if no manual records are needed.

```json
{
  "cname": [
    { "type": "CNAME", "host": "app.example.com", "value": "site.bench.example.com" }
  ],
  "a": [
    { "type": "A", "host": "app.example.com", "value": "203.0.113.10" }
  ]
}
```

Each set is a complete recipe. Add extra records to the same set when the user must create them together, such as TXT ownership records.

Exit `0` to proceed. Exit non-zero to abort and show stderr. Prefer fail-open behavior here during provider outages because `register` is the real gate.

## `register`

Claim and provision the route for `<domain>`. This runs before local site creation, so failure should leave no orphaned local site.

Stdout is ignored. Exit `0` only when the route is live. Exit non-zero to abort and show stderr.

`register` must be idempotent. A retry should re-apply the route, not duplicate it or fail because it already exists.

Fail closed when the control plane is unreachable.

## `deregister`

Remove the route and release the name. This runs after site drop and as rollback when creation fails midway.

Stdout is ignored. Always exit `0`; cleanup is best effort and should not break an otherwise successful local drop.

## `wildcard-domains`

Print wildcard patterns this host may create subdomains under.

```json
["*.region.example.com", "*.eu.example.com"]
```

Return blank for none. Prefer fail-soft behavior: blank stdout with exit `0` on provider outage.

## `proxy-servers`

Print edge proxy or load balancer IPs in front of this bench.

```json
["203.0.113.10", "203.0.113.11"]
```

When any IPs are returned, generated nginx accepts traffic only from those addresses and trusts their `X-Forwarded-For`. Return blank for direct-client nginx behavior.

Prefer fail-soft behavior here. A non-zero exit breaks nginx setup.

## Errors

Use one error mechanism: exit non-zero and write the message to stderr. Pilot shows stderr to the user and ignores stdout on failure.

```sh
echo "subdomain 'app' is already taken" >&2
exit 2
```

Suggested exit codes:

| Code | Meaning |
|------|---------|
| `0` | Success, fail-open, or fail-soft no-op |
| `1` | Transport or provider config failure |
| `2` | Declined: taken, reserved, invalid, or at limit |
| `64` | Usage error |

## Skeleton

```python
#!/usr/bin/env python3
import json
import sys


def main(argv):
    verb = argv[1] if len(argv) > 1 else ""

    if verb == "generate-dns-records" and len(argv) == 4:
        site, domain = argv[2], argv[3]
        return 0

    if verb == "register" and len(argv) == 3:
        domain = argv[2]
        return 0

    if verb == "deregister" and len(argv) == 3:
        return 0

    if verb == "wildcard-domains":
        print(json.dumps(["*.region.example.com"]))
        return 0

    if verb == "proxy-servers":
        print(json.dumps(["203.0.113.10"]))
        return 0

    print("usage: bench-domain-provider <verb> ...", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

## Test

```sh
./bench-domain-provider generate-dns-records mysite app.example.com
./bench-domain-provider wildcard-domains
./bench-domain-provider register taken.example.com
```

Install it on `PATH`, then test `bench new-site` with a wildcard-matching name and Add/Remove Domain in the Admin UI.
