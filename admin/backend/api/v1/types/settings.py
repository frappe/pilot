from __future__ import annotations

from typing import NotRequired, TypedDict

from admin.backend.api.responses import PageMeta
from pilot.core.bench.audit_log import AuditEntry


class AuditPage(TypedDict):
    data: list[AuditEntry]
    meta: PageMeta


class WorkerGroupSettings(TypedDict):
    queues: list[str]
    count: int


class FirewallRuleSettings(TypedDict):
    ip: str
    action: str
    description: str


class FirewallSettings(TypedDict):
    enabled: bool
    default: str
    rules: list[FirewallRuleSettings]


class WafConditionSettings(TypedDict):
    field: str
    operator: str
    value: str
    header_name: str


class WafRuleSettings(TypedDict):
    name: str
    action: str
    match: str
    enabled: bool
    conditions: list[WafConditionSettings]


class WafSettings(TypedDict):
    enabled: bool
    mode: str
    paranoia: int
    inbound_threshold: int
    body_limit: str
    inspect_responses: bool
    exclusions: list[str]
    exempt_paths: list[str]
    custom_rules: list[WafRuleSettings]


class S3Settings(TypedDict):
    access_key: str
    secret_key_set: bool
    bucket: str
    provider: str
    region: str
    endpoint_url: str


class LLMSettings(TypedDict):
    provider: str
    api_key_set: bool
    model: str
    max_tokens: int
    api_base: str


class LLMProviderOption(TypedDict):
    value: str
    label: str
    requires_api_base: bool
    free_text_model: bool
    models_need_api_key: bool


class MailSettings(TypedDict):
    server: str
    port: int
    email: str
    login: str
    use_ssl: bool
    password_set: bool


class WebhookEndpointSettings(TypedDict):
    url: str
    token_set: bool


class ResourceLimitSettings(TypedDict):
    cpu_usage_limit: int
    memory_usage_limit: int
    disk_space_limit: int
    site_uptime: bool
    webhook_endpoints: list[WebhookEndpointSettings]
    email_recipients: list[str]


class GeneralSettings(TypedDict):
    name: str
    python: str
    http_port: int
    socketio_port: int
    default_branch: str
    db_type: str
    allow_developer_mode: bool


class MariaDBSettings(TypedDict):
    host: str
    port: int
    admin_user: str
    socket_path: str


class PostgresSettings(TypedDict):
    host: str
    port: int
    admin_user: str
    password_set: bool


class RedisSettings(TypedDict):
    cache_port: int
    queue_port: int
    version: str


class WafSettingsDetail(WafSettings):
    installed: bool
    modes: list[str]
    rule_fields: list[str]
    rule_operators: list[str]
    rule_actions: list[str]
    rule_match: list[str]


class ProductionSettings(TypedDict):
    process_manager: str
    enabled: bool


class LiteModeSettings(TypedDict):
    enabled: bool
    supported: bool


class AdminDomainSettings(TypedDict):
    domain: str
    tls: bool


class LetsEncryptSettings(TypedDict):
    email: str


class S3ProviderOption(TypedDict):
    value: str
    label: str
    regions: list[str]


class LLMSettingsDetail(LLMSettings):
    system_prompt: str


class MonitorSettings(TypedDict):
    system_log_path: str
    log_path: str


class Settings(TypedDict):
    is_linux: bool
    native_process_manager: str
    bench: GeneralSettings
    mariadb: MariaDBSettings
    postgres: PostgresSettings
    redis: RedisSettings
    workers: list[WorkerGroupSettings]
    firewall: FirewallSettings
    waf: WafSettingsDetail
    production: ProductionSettings
    lite_mode: LiteModeSettings
    admin: AdminDomainSettings
    letsencrypt: LetsEncryptSettings
    s3: S3Settings
    s3_providers: list[S3ProviderOption]
    llm: LLMSettingsDetail
    llm_providers: list[LLMProviderOption]
    resource_limits: ResourceLimitSettings
    mail: MailSettings
    monitor: MonitorSettings


class SettingsUpdate(TypedDict):
    restarted: bool
    waf_warning: NotRequired[str]


class ClientAddress(TypedDict):
    ip: str
