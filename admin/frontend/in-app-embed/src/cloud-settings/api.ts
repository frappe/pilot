const METHOD_PREFIX = 'frappe.integrations.frappe_providers.cloud_settings'

const call = <T = any>(
  method: string,
  args: Record<string, unknown> = {},
  type: 'GET' | 'POST' = 'POST',
): Promise<T> =>
  new Promise((resolve, reject) => {
    const request = frappe.call({
      method: `${METHOD_PREFIX}.${method}`,
      args,
      type,
      silent: true,
      callback: (response) => resolve(response.message as T),
      error: (response) => reject(errorFromResponse(response)),
    })

    Promise.resolve(request).then(
      (response) => response && resolve(response.message as T),
      (exception) => reject(errorFromResponse(exception)),
    )
  })

const errorFromResponse = (response?: FrappeResponse) =>
  Object.assign(new Error(messageFromResponse(response)), {
    excType: response?.exc_type || response?.responseJSON?.exc_type || '',
  })

export const isMigrationConflict = (exception: unknown) =>
  (exception as { excType?: string } | undefined)?.excType === 'CloudMigrationConflictError'

const messageFromResponse = (response?: FrappeResponse) => {
  const raw = response?._server_messages || response?.responseJSON?._server_messages

  if (raw) {
    try {
      const messages = JSON.parse(raw)
        .map((item: string) => JSON.parse(item).message)
        .filter(Boolean)

      if (messages.length) return messages.join('. ').replace(/<[^>]*>/g, '')
    } catch {}
  }

  const exception = response?.exc_type || response?.responseJSON?.exc_type
  const status = response?.status || response?.httpStatus

  if (status === 403) return __("You don't have permission to do this.")
  if (exception) return __('{0}. Please try again.', [exception])

  return __('Something went wrong. Please try again.')
}

export const getContext = () => call('get_context', {}, 'GET')

export const getAccountUrl = () => call('get_account_url', {}, 'GET')

export const getBilling = () => call('get_billing', {}, 'GET')

export const getPlanOptions = ({
  provider,
  region,
}: {
  provider?: string
  region?: string
} = {}) => {
  const args: Record<string, string> = {}

  if (provider) args.provider = provider
  if (region) args.region = region

  return call('get_plan_options', args, 'GET')
}

export const changePlan = (plan: string) => call('change_plan', { plan })

export const getBillingProfile = () => call('get_billing_profile', {}, 'GET')

export const saveBillingProfile = (fields: Record<string, unknown>) =>
  call('save_billing_profile', fields)

export const removePaymentMethod = (name: string) =>
  call('remove_payment_method', { payment_method: name })

export const getPaymentGateways = () => call('get_payment_gateways', {}, 'GET')

export const addPaymentMethod = (methodType: string, gateway: string, contact: string) =>
  call('add_payment_method', {
    method_type: methodType,
    gateway,
    contact,
  })

export const confirmPaymentMethod = (payload: Record<string, unknown>) =>
  call('confirm_payment_method', payload)

export const createPaymentMethodCheckout = (redirectUrl: string, gateway: string) =>
  call('create_payment_method_checkout', {
    redirect_url: redirectUrl,
    gateway,
  })

export const confirmPaymentMethodCheckout = (reference: string) =>
  call('confirm_payment_method_checkout', { reference })

export const reconcilePaymentSetup = () => call('reconcile_payment_setup', {})

export const getMarketplaceApps = () => call('get_marketplace_apps', {}, 'GET')

export const installApp = (app: string) => call('install_app', { app })

export const uninstallApp = (app: string, mode?: string) =>
  call('uninstall_app', mode === 'disable' ? { app, mode } : { app })

export const updateApps = (apps?: string[]) => {
  const args = apps ? { apps: JSON.stringify(apps) } : {}

  return call('update_apps', args)
}

export const getTask = (taskId: string) => call('get_task', { task_id: taskId }, 'GET')

export const getDomains = () => call('get_domains', {}, 'GET')

export const getDomainDnsRecords = (domain: string) => call('get_domain_dns_records', { domain })

export const addDomain = (domain: string) => call('add_domain', { domain })

export const removeDomain = (domain: string) => call('remove_domain', { domain })

export const setPrimaryDomain = (domain: string) => call('set_primary_domain', { domain })

const pilotRequest = (
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  data?: Record<string, unknown>,
) => {
  const args = data ? { method, path, data: JSON.stringify(data) } : { method, path }

  return call('pilot_request', args, method === 'GET' ? 'GET' : 'POST')
}

export const getBackups = () => pilotRequest('GET', 'backups')

export const createBackup = () => pilotRequest('POST', 'backups')

export const deleteBackup = (timestamp: string) => pilotRequest('DELETE', `backups/${timestamp}`)

export const getBackupDownloadLinks = (timestamp: string) =>
  pilotRequest('GET', `backups/${timestamp}/download-links`)

export const getAnalytics = (window: string) => pilotRequest('GET', `monitoring?window=${window}`)

export const getUptime = (window: string) => pilotRequest('GET', `uptime?window=${window}`)

export const getStorage = () => pilotRequest('GET', 'storage')

export const refreshStorage = () => pilotRequest('POST', 'actions/refresh-storage')

export const getSiteConfig = () => pilotRequest('GET', 'configuration')

export const updateSiteConfig = (patch: Record<string, unknown>) =>
  pilotRequest('PATCH', 'configuration', patch)

export const clearCache = () => pilotRequest('POST', 'actions/clear-cache')

export const migrate = () => pilotRequest('POST', 'actions/migrate')

export const getErrorMessage = (exception: unknown, fallback?: string) =>
  (exception as Error | undefined)?.message || fallback || __('Something went wrong.')
