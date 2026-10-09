import { call } from './request.ts'
import type { Analytics, Backups, BillingProfile, BillingSummary, CloudContext, DnsRecord, Domains, Marketplace, PaymentCheckout, PaymentGateway, PaymentMethodSetup, PaymentStatus, PlanOptions, SiteConfiguration, Storage, TaskStatus, TaskSubmission, Uptime } from './types.ts'

export { CloudSettingsError, isMigrationConflict } from './request.ts'
export type * from './types.ts'


export const getContext = () => call<CloudContext>('get_context', {}, 'GET')

export const getAccountUrl = () => call<{ url: string }>('get_account_url', {}, 'GET')

export const getBilling = () => call<BillingSummary>('get_billing', {}, 'GET')

export interface PlanFilters {
  provider?: string
  region?: string
}

export const getPlanOptions = ({ provider, region }: PlanFilters = {}) => {
  const args: Record<string, string> = {}

  if (provider) args.provider = provider
  if (region) args.region = region

  return call<PlanOptions>('get_plan_options', args, 'GET')
}

export const changePlan = (plan: string) => call('change_plan', { plan })

export const getBillingProfile = () => call<BillingProfile>('get_billing_profile', {}, 'GET')

export const saveBillingProfile = (fields: Record<string, unknown>) =>
  call('save_billing_profile', fields)

export const removePaymentMethod = (name: string) =>
  call('remove_payment_method', { payment_method: name })

export const getPaymentGateways = () => call<PaymentGateway[]>('get_payment_gateways', {}, 'GET')

export const addPaymentMethod = (methodType: string, gateway: string, contact?: string | null) =>
  call<PaymentMethodSetup>('add_payment_method', {
    method_type: methodType,
    gateway,
    contact,
  })

export const confirmPaymentMethod = (payload: Record<string, unknown>) =>
  call<PaymentStatus>('confirm_payment_method', payload)

export const createPaymentMethodCheckout = (redirectUrl: string, gateway: string) =>
  call<PaymentCheckout>('create_payment_method_checkout', {
    redirect_url: redirectUrl,
    gateway,
  })

export const confirmPaymentMethodCheckout = (reference: string) =>
  call<PaymentStatus>('confirm_payment_method_checkout', { reference })

export const reconcilePaymentSetup = () => call<{ activated: string[] }>('reconcile_payment_setup', {})

export const getMarketplaceApps = () => call<Marketplace>('get_marketplace_apps', {}, 'GET')

export const installApp = (app: string) => call<TaskSubmission>('install_app', { app })

export const uninstallApp = (app: string, mode?: string) =>
  call<TaskSubmission>('uninstall_app', mode === 'disable' ? { app, mode } : { app })

export const updateApps = (apps?: string[]) => {
  const args = apps ? { apps: JSON.stringify(apps) } : {}

  return call<TaskSubmission>('update_apps', args)
}

export const getTask = (taskId: string) => call<TaskStatus>('get_task', { task_id: taskId }, 'GET')

export const getDomains = () => call<Domains>('get_domains', {}, 'GET')

export const getDomainDnsRecords = (domain: string) => call<{ records: DnsRecord[] }>('get_domain_dns_records', { domain })

export const addDomain = (domain: string) => call('add_domain', { domain })

export const removeDomain = (domain: string) => call('remove_domain', { domain })

export const setPrimaryDomain = (domain: string) => call('set_primary_domain', { domain })

const pilotRequest = <T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  data?: Record<string, unknown>,
) => {
  const args = data ? { method, path, data: JSON.stringify(data) } : { method, path }

  return call<T>('pilot_request', args, method === 'GET' ? 'GET' : 'POST')
}

export const getBackups = () => pilotRequest<Backups>('GET', 'backups')

export const createBackup = () => pilotRequest<TaskSubmission>('POST', 'backups')

export const deleteBackup = (timestamp: string) => pilotRequest<TaskSubmission>('DELETE', `backups/${encodeURIComponent(timestamp)}`)

export const getBackupDownloadLinks = (timestamp: string) =>
  pilotRequest<Record<string, string>>('GET', `backups/${encodeURIComponent(timestamp)}/download-links`)

export const getAnalytics = (window: string) => pilotRequest<Analytics>('GET', `monitoring?window=${encodeURIComponent(window)}`)

export const getUptime = (window: string) => pilotRequest<Uptime>('GET', `uptime?window=${encodeURIComponent(window)}`)

export const getStorage = () => pilotRequest<Storage>('GET', 'storage')

export const refreshStorage = () => pilotRequest<TaskSubmission>('POST', 'actions/refresh-storage')

export const getSiteConfig = () => pilotRequest<SiteConfiguration>('GET', 'configuration')

export const updateSiteConfig = (patch: Record<string, unknown>) =>
  pilotRequest<SiteConfiguration>('PATCH', 'configuration', patch)

export const clearCache = () => pilotRequest<TaskSubmission>('POST', 'actions/clear-cache')

export const migrate = () => pilotRequest<TaskSubmission>('POST', 'actions/migrate')
