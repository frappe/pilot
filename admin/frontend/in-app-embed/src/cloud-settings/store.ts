import {
  CloudSettingsError,
  getAccountUrl,
  getBilling,
  getDomains,
  getInvoices,
  getMarketplaceApps,
  getTask,
  isNotConnectedToCentral,
} from '@frappe/cloud-sdk/api'
import { reactive } from 'vue'
import type { AccountLinks, BillingSummary, CloudContext, Domains, Invoice, Marketplace, CloudSettingsTranslator, TaskStatus } from '@frappe/cloud-sdk'
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

export type TaskOutcome = 'success' | 'failed' | 'timeout' | 'gone' | 'error' | 'cancelled'

interface WaitOptions {
  /** Receives each poll result, or null while the task can't be read. */
  onUpdate?: (task: TaskStatus | null) => void
  maxWait?: number
}

export const waitForTask = async (
  taskId: string,
  isCancelled = () => false,
  { onUpdate, maxWait = MAX_WAIT }: WaitOptions = {},
): Promise<TaskOutcome> => {
  const deadline = Date.now() + maxWait

  while (!isCancelled()) {
    let task: TaskStatus | undefined

    try {
      task = await getTask(taskId)
    } catch {
      onUpdate?.(null)
      if (Date.now() > deadline) return 'error'

      await sleep(POLL_INTERVAL)
      continue
    }

    if (isCancelled()) return 'cancelled'

    onUpdate?.(task)
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

export interface AppTask {
  taskId: string
  verb: string
  task?: TaskStatus
  isUnreachable: boolean
}

// Module state, so an app task keeps its watcher, and its toast, after the dialog closes.
export const appTasks = reactive<Record<string, AppTask>>({})
const appTaskWaits = new Map<string, Promise<TaskOutcome>>()

/** Wait for an app task until it ends. A second call for the same task joins the first wait. */
export const watchAppTask = (name: string, taskId: string, verb: string) => {
  const joined = appTaskWaits.get(taskId)
  if (joined) return { outcome: joined, isJoined: true }

  appTasks[name] = { taskId, verb, isUnreachable: false }
  const onUpdate = (task: TaskStatus | null) => {
    const entry = appTasks[name]
    if (entry?.taskId !== taskId) return

    entry.isUnreachable = !task
    if (task) entry.task = task
  }

  const outcome = waitForTask(taskId, undefined, { onUpdate, maxWait: Infinity })
  appTaskWaits.set(taskId, outcome)
  outcome.finally(() => appTaskWaits.delete(taskId))

  return { outcome, isJoined: false }
}

export const createStore =(context?: CloudContext, translator: CloudSettingsTranslator = translate) => {
  const state = reactive({
    context: context || { enabled: false },
    // Loaded before a click, because a browser blocks a tab opened after an await.
    accountLinks: null as AccountLinks | null,
    accountLinksError: '',
    billing: null as BillingSummary | null,
    billingError: '',
    isNotConnectedToCentral: false,
    invoices: null as Invoice[] | null,
    invoicesError: '',
    marketplace: null as Marketplace | null,
    marketplaceError: '',
    domains: null as Domains | null,
    domainsError: '',
  })

  const loadAccountLinks = async () => {
    state.accountLinksError = ''

    if (state.accountLinks) return

    try {
      state.accountLinks = await getAccountUrl()
    } catch (exception) {
      state.accountLinksError = getErrorMessage(exception, undefined, translator)
    }
  }

  const loadBilling = async (force = false) => {
    state.billingError = ''

    if (state.billing && !force) return

    try {
      state.billing = await getBilling()
    } catch (exception) {
      state.isNotConnectedToCentral = isNotConnectedToCentral(exception)
      state.billingError = getErrorMessage(exception, undefined, translator)
    }
  }

  const loadInvoices = async () => {
    state.invoicesError = ''

    try {
      state.invoices = await getInvoices()
    } catch (exception) {
      state.invoicesError = getErrorMessage(exception, undefined, translator)
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

  const isOnTrial = () => Boolean(state.billing?.trial)

  return {
    state,
    isOnTrial,
    loadAccountLinks,
    loadBilling,
    loadInvoices,
    loadMarketplace,
    loadDomains,
  }
}

export type Store = ReturnType<typeof createStore>
