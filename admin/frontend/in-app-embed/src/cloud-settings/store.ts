import {
  CloudSettingsError,
  getBilling,
  getDomains,
  getMarketplaceApps,
  getTask,
} from '@frappe/cloud-sdk/api'
import { reactive } from 'vue'
import type { BillingSummary, CloudContext, Domains, Marketplace, CloudSettingsTranslator } from '@frappe/cloud-sdk'
import { translate, useTranslation } from './translation'
import { toast } from 'frappe-ui'

export const getErrorMessage = (
  exception: unknown,
  fallback?: string,
  __: CloudSettingsTranslator = translate,
) => {
  if (!(exception instanceof CloudSettingsError)) {
    return (exception instanceof Error ? exception.message : '') || fallback || __('Something went wrong.')
  }

  if (exception.serverMessages.length) return exception.message
  if (exception.status === 0) return __('Could not reach Cloud Settings. Please try again.')
  if (exception.status === 403) return __("You don't have permission to do this.")
  if (exception.excType) return __('{0}. Please try again.', [exception.excType])

  return __('Something went wrong. Please try again.')
}

export const useErrorMessage = () => {
  const translate = useTranslation()
  return (exception: unknown, fallback?: string) => getErrorMessage(exception, fallback, translate)
}

const POLL_INTERVAL = 2500
const MAX_WAIT = 3 * 60 * 1000

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

type TaskOutcome = 'success' | 'failed' | 'timeout' | 'gone' | 'error' | 'cancelled'

export const waitForTask = async (
  taskId: string,
  isCancelled = () => false,
): Promise<TaskOutcome> => {
  const deadline = Date.now() + MAX_WAIT

  while (!isCancelled()) {
    let task: { status?: string; exit_code?: number | null } | undefined

    try {
      task = await getTask(taskId)
    } catch {
      if (Date.now() > deadline) return 'error'

      await sleep(POLL_INTERVAL)
      continue
    }

    if (isCancelled()) return 'cancelled'

    const status = task?.status

    if (!status) return 'gone'

    if (!['success', 'failed', 'killed'].includes(status)) {
      if (Date.now() > deadline) return 'timeout'

      await sleep(POLL_INTERVAL)
      continue
    }

    const succeeded = status === 'success' && (task?.exit_code === 0 || task?.exit_code == null)

    return succeeded ? 'success' : 'failed'
  }

  return 'cancelled'
}

export const settleTask = async (
  taskId: string,
  isCancelled: () => boolean,
  failure: string,
  __: CloudSettingsTranslator = translate,
) => {
  const outcome = await waitForTask(taskId, isCancelled)

  if (outcome === 'success') return true
  if (outcome === 'failed' || outcome === 'error') throw new Error(failure)

  if (outcome !== 'cancelled') {
    toast.warning(__('Still running in the background. Check back in a bit.'))
  }

  return false
}

type RememberedTask = { taskId: string; verb: string }

const tasksKey = (site: string) => `cloud-settings:tasks:${site}`

export const getRememberedTasks = (site: string): Record<string, RememberedTask> => {
  try {
    return JSON.parse(localStorage.getItem(tasksKey(site)) || '{}')
  } catch {
    return {}
  }
}

export const rememberTask = (site: string, name: string, task?: RememberedTask) => {
  const tasks = getRememberedTasks(site)

  if (task) tasks[name] = task
  else delete tasks[name]

  try {
    localStorage.setItem(tasksKey(site), JSON.stringify(tasks))
  } catch {}
}

export const createStore = (context?: CloudContext, translator: CloudSettingsTranslator = translate) => {
  const state = reactive({
    context: context || { enabled: false },
    billing: null as BillingSummary | null,
    billingError: '',
    marketplace: null as Marketplace | null,
    marketplaceError: '',
    domains: null as Domains | null,
    domainsError: '',
  })

  const loadBilling = async (force = false) => {
    state.billingError = ''

    if (state.billing && !force) return

    try {
      state.billing = await getBilling()
    } catch (exception) {
      state.billingError = getErrorMessage(exception, undefined, translator)
    }
  }

  const loadMarketplace = async (force = false) => {
    state.marketplaceError = ''

    if (state.marketplace && !force) return

    try {
      state.marketplace = await getMarketplaceApps()
    } catch (exception) {
      state.marketplaceError = getErrorMessage(exception, undefined, translator)
    }
  }

  const loadDomains = async (force = false) => {
    state.domainsError = ''

    if (state.domains && !force) return

    try {
      state.domains = await getDomains()
    } catch (exception) {
      state.domainsError = getErrorMessage(exception, undefined, translator)
    }
  }

  return {
    state,
    loadBilling,
    loadMarketplace,
    loadDomains,
  }
}

export type Store = ReturnType<typeof createStore>
