export interface CloudContext {
  enabled: boolean
  provider?: string
  site_name?: string
  server_url?: string
  account_url?: string
  bundle?: { js?: string }
}

/** Central dashboard links for this site. */
export interface AccountLinks {
  url: string
  billing: string
  invoices: string
  change_plan: string
  server: string
  /** Opens this server's terminate dialog in Frappe Cloud, never the server list. */
  end_subscription: string
}

export interface TaskSubmission { task_id: string }
export interface TaskStep { key: string; label: string; status: 'running' | 'done' | 'failed'; started_at: number }
export interface TaskStatus {
  status?: string
  exit_code?: number | null
  command?: string
  started_at?: string | null
  queue_position?: number | null
  steps?: TaskStep[]
  /** The running step's latest output line, and its percentage when the line is a progress bar. */
  activity?: string | null
  percent?: number | null
  /** The task's last output line, when it failed. */
  error?: string | null
}

export interface BillingSummary {
  currency: string
  plan: { name: string; subtitle: string; specs: Record<string, string> } | null
  estimate: { amount: string; note: string }
  /** `available` is true when the team has credit, which Central uses before the card. */
  credit: { amount: string; note: string; warning: boolean; available?: boolean }
  payment_method: { name: string; label: string } | null
  profile_complete: boolean
  usage?: { name: string; percent: number; detail: string }[]
  /** The site's open free trial. Absent or null when the site is not on one. */
  trial?: BillingTrial | null
  /** The server's pending plan change, such as a converted trial's move to its paid plan. */
  plan_change?: { plan: string | null; progress_url: string | null } | null
  /** This site's server's share of the team's next bill. Absent before Central sends it. */
  next_bill?: NextBill
}

export interface NextBill {
  /** Formatted money, tax included. */
  amount: string
  subtotal: string
  tax: string | null
  /** Such as GST or VAT, when the bill carries tax. */
  tax_type: string | null
  /** ISO date of the team's next charge. */
  charge_on: string | null
  days_left: number
  lines: { item: string; detail: string | null; amount: string }[]
  /** What would stop the team's next charge. Empty when nothing in the data would. */
  blockers: { code: string; title: string; fix: string | null }[]
}

/** This site's server's share of one team invoice. */
export interface Invoice {
  name: string
  period_start: string
  period_end: string
  status: 'Outstanding' | 'Paid' | 'Overdue' | 'Waived'
  /** Formatted money, tax included. */
  amount: string
  /** The invoice covers this server alone and has been issued. */
  has_pdf: boolean
  /** The invoice covers this server alone and is open. */
  can_pay: boolean
}

export interface InvoicePdf { filename: string; content: string }

export interface BillingTrial {
  state: 'Active' | 'Ended'
  ends_on: string
  days_left: number
  /** When an ended trial's server is deleted. */
  deletes_on: string | null
  /** The team has a payment method, but the plan is above its spending limit, so the trial stays open. */
  held?: boolean
  /** The plan the site converts to, at the rate its subscription charges. */
  plan?: BillingTrialPlan | null
  /** The app the trial is for. It stays installed until the site converts. */
  app?: string | null
}

export interface BillingTrialPlan {
  name: string
  title: string
  /** Null until the team has a billing currency. */
  monthly_rate: number | null
  currency: string | null
}

export interface PlanOption {
  name: string
  title: string
  subtitle: string
  /** Who the plan suits, as the site's product describes it, or null. */
  note?: string | null
  price: string
  is_current: boolean
}

export interface PlanOptions {
  currency: string
  provider: string | null
  region: string | null
  current: string | null
  /** On a free trial, the paid plan the site moves to when the trial converts. */
  selected?: string | null
  /** Cheapest first. */
  plans: PlanOption[]
  sufficient: boolean
}

/** A queued plan change. `progress_url` is the page that follows it while the site restarts.
 *  On a free trial nothing is queued: Central keeps the plan for the conversion. */
export interface PlanChange {
  queued: boolean
  resized: boolean
  progress_url: string | null
}

export interface BillingProfile {
  currency: string
  legal_name: string
  email: string
  phone?: string
  address_line1: string
  city: string
  state: string
  country: string
  pincode: string
  gstin: string
  supported_currencies?: (string | { label: string; value: string })[]
  /** Money has moved in this currency, so Central refuses to change it. */
  currency_locked?: boolean
  /** Country names the address form picks from. */
  countries?: string[]
  /** India's GST state names, the only state list Central checks. */
  india_states?: string[]
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
  /** The installed apps that need this one. Frappe refuses to uninstall it while they stay. */
  required_by?: string[]
  repo?: string
  documentation?: string
  website?: string
  dependencies?: string[]
  nightly?: boolean
  frappe_version?: string
  categories?: string[]
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
export interface DnsRecords { cname?: DnsRecord[]; a?: DnsRecord[] }

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
