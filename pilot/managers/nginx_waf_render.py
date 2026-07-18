from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers.waf import SHARED_MODSEC_DIR

if TYPE_CHECKING:
    from pilot.config.waf import WafCondition, WafConfig, WafRule
    from pilot.core.bench import Bench


_WAF_FIELD_VARS = {
    "uri_path": "REQUEST_FILENAME",
    "uri_full": "REQUEST_URI",
    "query": "QUERY_STRING",
    "method": "REQUEST_METHOD",
    "source_ip": "REMOTE_ADDR",
    "user_agent": "REQUEST_HEADERS:User-Agent",
    "host": "REQUEST_HEADERS:Host",
}
_WAF_OPERATORS = {
    "is": "@streq",
    "is_not": "@streq",
    "contains": "@contains",
    "not_contains": "@contains",
    "starts_with": "@beginsWith",
    "matches": "@rx",
}
_WAF_NEGATED_OPERATORS = {"is_not", "not_contains"}
_WAF_ACTION_DIRECTIVES = {
    "block": "deny,status:403,log",
    "log": "pass,log,auditlog",
    "skip": "pass,ctl:ruleEngine=Off",
}


class ModSecurityRenderer:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def modsec_dir(self) -> Path:
        return self.bench.config_path / "modsecurity"

    def render_main(self, modsec_dir: Path) -> str:
        return (
            f"Include {modsec_dir}/modsecurity.conf\n"
            f"Include {SHARED_MODSEC_DIR}/crs-setup.conf\n"
            f"Include {modsec_dir}/overrides.conf\n"
            f"Include {modsec_dir}/custom_rules.conf\n"
            f"Include {SHARED_MODSEC_DIR}/rules/*.conf\n"
            f"Include {modsec_dir}/exclusions.conf\n"
        )

    def render_engine(self, waf: "WafConfig") -> str:
        from pilot.config.waf import parse_nginx_size

        audit_log = self.bench.path / "logs" / "modsec_audit.log"
        body_action = "Reject" if waf.mode == "On" else "ProcessPartial"
        response_access = "On" if waf.inspect_responses else "Off"
        return (
            f"SecRuleEngine {waf.mode}\n"
            "SecRequestBodyAccess On\n"
            f"SecRequestBodyLimit {parse_nginx_size(waf.body_limit)}\n"
            "SecRequestBodyNoFilesLimit 131072\n"
            f"SecRequestBodyLimitAction {body_action}\n"
            "SecRequestBodyJsonDepthLimit 512\n"
            f"SecResponseBodyAccess {response_access}\n"
            "SecResponseBodyMimeType text/plain text/html text/xml application/json\n"
            "SecResponseBodyLimit 524288\n"
            "SecAuditEngine RelevantOnly\n"
            "SecAuditLogFormat JSON\n"
            "SecAuditLogType Serial\n"
            f"SecAuditLog {audit_log}\n"
            "SecAuditLogParts ABIJDEFHZ\n"
            "SecTmpDir /tmp\n"
            "SecDataDir /tmp\n"
            'SecDefaultAction "phase:1,pass,log"\n'
            'SecDefaultAction "phase:2,pass,log"\n'
        )

    @staticmethod
    def render_overrides(waf: "WafConfig") -> str:
        lines = [
            f'SecAction "id:1000,phase:1,pass,nolog,'
            f"setvar:tx.blocking_paranoia_level={waf.paranoia},"
            f"setvar:tx.detection_paranoia_level={waf.paranoia},"
            f'setvar:tx.paranoia_level={waf.paranoia}"',
            f'SecAction "id:1001,phase:1,pass,nolog,'
            f'setvar:tx.inbound_anomaly_score_threshold={waf.inbound_threshold}"',
        ]
        for index, path in enumerate(waf.exempt_paths):
            lines.append(
                f'SecRule REQUEST_URI "@beginsWith {path}" '
                f'"id:{10000 + index},phase:1,pass,nolog,ctl:ruleEngine=Off"'
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def render_exclusions(waf: "WafConfig") -> str:
        return "\n".join(waf.exclusions) + ("\n" if waf.exclusions else "")

    @classmethod
    def render_custom_rules(cls, waf: "WafConfig") -> str:
        blocks = []
        for index, rule in enumerate(waf.custom_rules):
            if not rule.enabled or not rule.conditions:
                continue
            base_id = 100000 + index * 100
            action = _WAF_ACTION_DIRECTIVES[rule.action]
            msg = f"msg:'Custom rule: {rule.name or 'unnamed'}'"
            if rule.match == "any":
                blocks.append(cls._render_any_rule(rule, base_id, action, msg))
            else:
                blocks.append(cls._render_all_rule(rule, base_id, action, msg))
        return "\n".join(blocks) + ("\n" if blocks else "")

    @staticmethod
    def _condition_var_op(cond: "WafCondition") -> tuple[str, str]:
        if cond.field == "header":
            variable = f"REQUEST_HEADERS:{cond.header_name}"
        else:
            variable = _WAF_FIELD_VARS[cond.field]
        negate = "!" if cond.operator in _WAF_NEGATED_OPERATORS else ""
        if cond.field == "source_ip" and cond.operator in ("is", "is_not"):
            operator = "@ipMatch"
            value = ",".join(entry.strip() for entry in cond.value.split(","))
        else:
            operator = _WAF_OPERATORS[cond.operator]
            value = cond.value
        return variable, f"{negate}{operator} {value}"

    @classmethod
    def _render_all_rule(cls, rule: "WafRule", base_id: int, action: str, msg: str) -> str:
        conditions = rule.conditions
        lines = []
        for position, cond in enumerate(conditions):
            variable, operator_arg = cls._condition_var_op(cond)
            last = position == len(conditions) - 1
            if position == 0:
                actions = [f"id:{base_id}", "phase:1", action, msg]
                if not last:
                    actions.append("chain")
                lines.append(f'SecRule {variable} "{operator_arg}" "{",".join(actions)}"')
            else:
                tail = ' "chain"' if not last else ""
                lines.append(f'    SecRule {variable} "{operator_arg}"{tail}')
        return "\n".join(lines)

    @classmethod
    def _render_any_rule(cls, rule: "WafRule", base_id: int, action: str, msg: str) -> str:
        lines = []
        for position, cond in enumerate(rule.conditions):
            variable, operator_arg = cls._condition_var_op(cond)
            actions = ",".join([f"id:{base_id + position}", "phase:1", action, msg])
            lines.append(f'SecRule {variable} "{operator_arg}" "{actions}"')
        return "\n".join(lines)
