import re
from dataclasses import dataclass, field

from pilot.exceptions import ConfigError

# Accepted WAF modes, in UI order.
WAF_MODES = ("Off", "DetectionOnly", "On")

# Custom-rule vocabulary shared by validation, rendering, API, and UI.
WAF_RULE_FIELDS = ("uri_path", "uri_full", "query", "method", "source_ip", "user_agent", "header", "host")
WAF_RULE_OPERATORS = ("is", "is_not", "contains", "not_contains", "starts_with", "matches")
WAF_RULE_ACTIONS = ("block", "log", "skip")
WAF_RULE_MATCH = ("all", "any")

_SIZE_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}
_WAF_EXEMPT_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._~%/-]*$")
# A custom-rule condition value is interpolated into a quoted SecLang operator
# argument. Regex metacharacters are allowed (the "matches" operator), but a
# double quote, newline, or NUL would break out of the string and inject
# directives, so those are forbidden. Header names must be plain HTTP tokens.
_WAF_RULE_VALUE_FORBIDDEN = re.compile(r'["\n\r\x00]')
# The rule name is wrapped in a single-quoted SecLang msg inside a double-quoted
# action list, so it must contain neither quote style nor a newline.
_WAF_RULE_NAME_FORBIDDEN = re.compile("[\"'\n\r\x00]")
_WAF_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_WAF_MAX_RULES = 100
_WAF_MAX_CONDITIONS = 10
_WAF_MAX_VALUE_LEN = 1024


def parse_nginx_size(value: str) -> int:
    """Parse nginx sizes like '50m', '1g', or raw byte counts."""
    text = str(value).strip().lower()
    if not text:
        raise ValueError("empty size")
    if text[-1] in _SIZE_UNITS:
        return int(text[:-1]) * _SIZE_UNITS[text[-1]]
    return int(text)


@dataclass
class WafCondition:
    """One predicate in a custom WAF rule."""

    field: str
    operator: str
    value: str
    header_name: str = ""


@dataclass
class WafRule:
    """A custom rule compiled to ModSecurity SecRule(s)."""

    name: str
    action: str = "block"
    match: str = "all"
    enabled: bool = True
    conditions: list[WafCondition] = field(default_factory=list)


@dataclass
class WafConfig:
    """ModSecurity/OWASP CRS settings applied to every nginx vhost."""

    enabled: bool = False
    mode: str = "DetectionOnly"  # "DetectionOnly" | "On" | "Off"
    paranoia: int = 1
    inbound_threshold: int = 5
    body_limit: str = "50m"
    inspect_responses: bool = False
    exclusions: list[str] = field(default_factory=list)
    exempt_paths: list[str] = field(default_factory=list)
    custom_rules: list[WafRule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "WafConfig":
        if not data:
            return cls()
        # Let validate() report non-integers as ConfigError.
        return cls(
            enabled=bool(data.get("enabled", False)),
            mode=str(data.get("mode", "DetectionOnly")),
            paranoia=data.get("paranoia", 1),
            inbound_threshold=data.get("inbound_threshold", 5),
            body_limit=str(data.get("body_limit", "50m")),
            inspect_responses=bool(data.get("inspect_responses", False)),
            exclusions=[str(line) for line in data.get("exclusions", [])],
            exempt_paths=[str(path) for path in data.get("exempt_paths", [])],
            custom_rules=[cls._parse_rule(rule) for rule in data.get("custom_rules", [])],
        )

    @staticmethod
    def _parse_rule(data: dict) -> WafRule:
        # Values pass through as strings; validate() is the authoritative check.
        return WafRule(
            name=str(data.get("name", "")),
            action=str(data.get("action", "block")),
            match=str(data.get("match", "all")),
            enabled=bool(data.get("enabled", True)),
            conditions=[
                WafCondition(
                    field=str(cond.get("field", "")),
                    operator=str(cond.get("operator", "")),
                    value=str(cond.get("value", "")),
                    header_name=str(cond.get("header_name", "")),
                )
                for cond in data.get("conditions", [])
            ],
        )

    def validate(self, nginx_max_body_size: str) -> None:
        if self.mode not in WAF_MODES:
            raise ConfigError(f"waf.mode '{self.mode}' is invalid. Must be one of: {', '.join(WAF_MODES)}.")
        if not isinstance(self.paranoia, int) or not 1 <= self.paranoia <= 4:
            raise ConfigError(f"waf.paranoia '{self.paranoia}' is invalid. Must be an integer between 1 and 4.")
        if not isinstance(self.inbound_threshold, int) or self.inbound_threshold < 1:
            raise ConfigError(f"waf.inbound_threshold '{self.inbound_threshold}' is invalid. Must be a positive integer.")
        try:
            body_limit = parse_nginx_size(self.body_limit)
        except ValueError:
            raise ConfigError(f"waf.body_limit '{self.body_limit}' is not a valid size (e.g. '50m', '13107200').")
        # Coupling only matters when the WAF is on: a body larger than what
        # ModSecurity buffers would be proxied to the app uninspected.
        if self.enabled and body_limit < parse_nginx_size(nginx_max_body_size):
            raise ConfigError(
                f"waf.body_limit '{self.body_limit}' must be >= nginx.client_max_body_size "
                f"'{nginx_max_body_size}', else large uploads bypass inspection."
            )
        for i, path in enumerate(self.exempt_paths):
            if len(path) > 255 or not _WAF_EXEMPT_PATH_PATTERN.match(path):
                raise ConfigError(
                    f"waf.exempt_paths[{i}] '{path}' is invalid. Must be a URL path starting "
                    f"with '/' using only letters, digits, and . _ ~ % / - characters."
                )
        self._validate_custom_rules()

    def _validate_custom_rules(self) -> None:
        """Validate SecLang-bound custom rule input."""
        if len(self.custom_rules) > _WAF_MAX_RULES:
            raise ConfigError(f"waf.custom_rules has too many rules (max {_WAF_MAX_RULES}).")
        for i, rule in enumerate(self.custom_rules):
            prefix = f"waf.custom_rules[{i}]"
            if rule.action not in WAF_RULE_ACTIONS:
                raise ConfigError(f"{prefix}.action '{rule.action}' is invalid. Must be one of: {', '.join(WAF_RULE_ACTIONS)}.")
            if rule.match not in WAF_RULE_MATCH:
                raise ConfigError(f"{prefix}.match '{rule.match}' is invalid. Must be one of: {', '.join(WAF_RULE_MATCH)}.")
            if _WAF_RULE_NAME_FORBIDDEN.search(rule.name) or len(rule.name) > 128:
                raise ConfigError(f"{prefix}.name is invalid. Must be under 128 chars with no quotes or newlines.")
            if not rule.conditions:
                raise ConfigError(f"{prefix} must have at least one condition.")
            if len(rule.conditions) > _WAF_MAX_CONDITIONS:
                raise ConfigError(f"{prefix} has too many conditions (max {_WAF_MAX_CONDITIONS}).")
            for j, cond in enumerate(rule.conditions):
                self._validate_condition(f"{prefix}.conditions[{j}]", cond)

    @staticmethod
    def _validate_condition(prefix: str, cond: WafCondition) -> None:
        if cond.field not in WAF_RULE_FIELDS:
            raise ConfigError(f"{prefix}.field '{cond.field}' is invalid. Must be one of: {', '.join(WAF_RULE_FIELDS)}.")
        if cond.operator not in WAF_RULE_OPERATORS:
            raise ConfigError(f"{prefix}.operator '{cond.operator}' is invalid. Must be one of: {', '.join(WAF_RULE_OPERATORS)}.")
        if not cond.value:
            raise ConfigError(f"{prefix}.value is required.")
        if len(cond.value) > _WAF_MAX_VALUE_LEN or _WAF_RULE_VALUE_FORBIDDEN.search(cond.value):
            raise ConfigError(f"{prefix}.value is invalid. Must be under {_WAF_MAX_VALUE_LEN} chars with no double quotes or newlines.")
        if cond.field == "header" and not _WAF_HEADER_NAME_PATTERN.match(cond.header_name):
            raise ConfigError(f"{prefix}.header_name '{cond.header_name}' is invalid. Must be an HTTP header token (letters, digits, hyphen).")
        if cond.field == "source_ip":
            import ipaddress

            for entry in cond.value.split(","):
                try:
                    ipaddress.ip_network(entry.strip(), strict=False)
                except ValueError:
                    raise ConfigError(f"{prefix}.value '{entry.strip()}' is not a valid IP or CIDR range.")
