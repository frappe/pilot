import { reactive } from 'vue'

import { authApi } from '@/api/auth'

const session = reactive({
  loaded: false,
  authenticated: false,
  wizard: false,
  pending: false,
  enabled: false,
  benchName: '',
  allowBenchManagement: false,
  developerMode: false,
  centralEnabled: false,
  centralUrl: '',
  centralAudience: '',
})

const loadSession = async () => {
  try {
    const [bootstrap, currentSession] = await Promise.all([authApi.bootstrap(), authApi.session()])
    session.authenticated = currentSession.authenticated === true
    session.wizard = bootstrap.mode === 'setup'
    session.pending = bootstrap.mode === 'pending'
    session.enabled = bootstrap.enabled === true
    session.benchName = bootstrap.name || ''
    session.allowBenchManagement = bootstrap.allow_bench_management === true
    session.developerMode = bootstrap.developer_mode === true
    session.centralEnabled = bootstrap.central === true
    session.centralUrl = bootstrap.central_url || ''
    session.centralAudience = bootstrap.central_audience || ''
  } catch {
    session.authenticated = false
    session.wizard = false
    session.pending = false
    session.enabled = false
    session.benchName = ''
    session.allowBenchManagement = false
    session.developerMode = false
    session.centralEnabled = false
    session.centralUrl = ''
    session.centralAudience = ''
  }
  session.loaded = true
}

const ensureSession = async () => {
  if (!session.loaded) await loadSession()
}

export type CentralServerAction = 'overview' | 'resize'

/** This server's page in Central, opened on `action`. Empty when Central is not set up. */
const centralServerUrl = (action: CentralServerAction = 'overview'): string => {
  if (!session.centralUrl) return ''
  if (!session.centralAudience) return `${session.centralUrl}/dashboard/servers`
  const query = new URLSearchParams({ pilot: session.centralAudience, action })
  return `${session.centralUrl}/dashboard/servers?${query}`
}

export const useSession = () => {
  return { session, loadSession, ensureSession, centralServerUrl }
}
