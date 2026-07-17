from dataclasses import dataclass, field

# The only accepted values for WafConfig.mode, in UI order. Single source of
# truth: validation (bench_config._validate_waf), the settings API, and the
# admin UI all reference this rather than repeating the literals.
WAF_MODES = ("Off", "DetectionOnly", "On")

# Custom-rule vocabulary. Single source of truth shared by validation, the
# SecRule compiler (nginx_manager), the settings API, and the builder UI.
# ``field`` -> the request part matched; ``operator`` -> how; ``action`` -> what
# happens on a match; ``match`` -> how a rule's conditions combine.
WAF_RULE_FIELDS = ("uri_path", "uri_full", "query", "method", "source_ip", "user_agent", "header", "host")
WAF_RULE_OPERATORS = ("is", "is_not", "contains", "not_contains", "starts_with", "matches")
WAF_RULE_ACTIONS = ("block", "log", "skip")
WAF_RULE_MATCH = ("all", "any")

_SIZE_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


def parse_nginx_size(value: str) -> int:
    """Bytes for an nginx size string ('50m', '1g', '13107200'). Suffixes k/m/g
    are powers of 1024; a bare number is bytes. Raises ValueError on garbage."""
    text = str(value).strip().lower()
    if not text:
        raise ValueError("empty size")
    if text[-1] in _SIZE_UNITS:
        return int(text[:-1]) * _SIZE_UNITS[text[-1]]
    return int(text)


@dataclass
class WafCondition:
    """One predicate of a custom rule: match ``field`` against ``value`` with
    ``operator``. ``header_name`` names the request header when ``field`` is
    "header" (ignored otherwise)."""

    field: str
    operator: str
    value: str
    header_name: str = ""


@dataclass
class WafRule:
    """A Cloudflare-style custom rule that compiles to ModSecurity SecRule(s):
    when the ``conditions`` match (all of them for ``match`` "all", any of them
    for "any"), apply ``action`` ("block" | "log" | "skip"). ``name`` is a label
    surfaced in the audit log; ``enabled`` toggles the rule without deleting it."""

    name: str
    action: str = "block"
    match: str = "all"
    enabled: bool = True
    conditions: list[WafCondition] = field(default_factory=list)


@dataclass
class WafConfig:
    """ModSecurity (layer-7 WAF) settings applied to every nginx vhost of the bench.

    Runs the OWASP Core Rule Set. ``mode`` maps to ``SecRuleEngine``:
    "DetectionOnly" logs matches without blocking (the safe default for a
    monitor-first rollout), "On" enforces, "Off" disables inspection. ``enabled``
    is the master switch; when off, no ModSecurity directives are emitted at all.

    ``paranoia`` is the CRS blocking paranoia level (1 = fewest false positives,
    4 = most aggressive). ``inbound_threshold`` is the anomaly score at which a
    request is blocked. ``body_limit`` caps the request body ModSecurity buffers
    for inspection and must be >= nginx's ``client_max_body_size``. ``exclusions``
    are raw SecLang lines (e.g. ``SecRuleRemoveById``) applied after the CRS to
    silence false positives; ``exempt_paths`` are location prefixes that bypass
    the WAF entirely.
    """

    enabled: bool = False
    mode: str = "DetectionOnly"  # "DetectionOnly" | "On" | "Off"
    paranoia: int = 1
    inbound_threshold: int = 5
    body_limit: str = "50m"
    inspect_responses: bool = False
    exclusions: list[str] = field(default_factory=list)
    exempt_paths: list[str] = field(default_factory=list)
    custom_rules: list[WafRule] = field(default_factory=list)
