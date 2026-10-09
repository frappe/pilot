export interface CloudContext {
  enabled: boolean
  provider?: string
  site_name?: string
  server_url?: string
  account_url?: string
  bundle?: { js?: string }
}

export interface TaskSubmission { task_id: string }
export interface TaskStatus { status?: string; exit_code?: number | null }

export interface BillingSummary {
  currency: string
  plan: { name: string; subtitle: string; specs: Record<string, string> } | null
  estimate: { amount: string; note: string }
  credit: { amount: string; note: string; warning: boolean }
  payment_method: { name: string; label: string } | null
  profile_complete: boolean
  usage?: { name: string; percent: number; detail: string }[]
}

export interface PlanOption {
  name: string
  title: string
  subtitle: string
  price: string
  is_current: boolean
}

export interface PlanOptions {
  currency: string
  provider: string | null
  region: string | null
  current: string | null
  plans: PlanOption[]
  sufficient: boolean
}

export interface BillingProfile {
  currency: string
  legal_name: string
  email: string
  address_line1: string
  city: string
  state: string
  country: string
  pincode: string
  gstin: string
  supported_currencies?: (string | { label: string; value: string })[]
}

export interface PaymentGateway {
  name: string
  label: string
  adapter_key: string
  method_types: string[]
  subtitle?: string
}

export interface PaymentMethodSetup {
  key_id: string
  order_id: string
  customer_id: string
  recurring?: boolean
  payment_method: string
  prefill?: Record<string, string>
}

export interface PaymentStatus { status: string; active?: boolean; label?: string; message?: string }
export interface PaymentCheckout { checkout_url: string; reference: string }

export interface MarketplaceApp {
  name: string
  title: string
  description: string
  logo_url: string
  category: string
  stars: number
  installable: boolean
  required_version: string
  installed: boolean
  installed_version: string
  latest_version: string
  has_update: boolean
}

export interface Marketplace {
  can_disable?: boolean
  apps: MarketplaceApp[]
  categories: string[]
  update_count: number
}

export interface Domain {
  domain: string
  is_primary: boolean
  is_default: boolean
  tls?: boolean
  public_scheme?: string
}

export interface Domains { primary: string; domains: Domain[] }
export interface DnsRecord { type: string; host: string; value: string }

export interface BackupFile {
  filename: string
  path: string
  size_bytes: number
  kind: string
}

export interface Backup {
  timestamp: string
  created_at: string
  is_offsite: boolean
  files: BackupFile[]
}

export type Backups = Backup[]

export interface Timeline {
  categories: string[]
  points: { time: number; [category: string]: number }[]
}

export interface Analytics {
  now: number
  window_seconds: number
  requests_over_time: Timeline
  background_jobs_over_time: Timeline
  top_paths: Timeline
}

export interface Uptime {
  overall_percent: number | null
  buckets: { time: number; checks: number; percent: number }[]
}

export interface Storage {
  database_bytes: number
  bytes: number
  private_files_bytes: number
  public_files_bytes: number
  backups_bytes: number
  other_bytes: number
  collected_at: string
  backup_files: { name: string; bytes: number }[]
  other_entries: { name: string; bytes: number }[]
}

export type SiteConfiguration = Record<string, unknown>
