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
  } catch {
    session.authenticated = false
    session.wizard = false
    session.pending = false
    session.enabled = false
    session.benchName = ''
    session.allowBenchManagement = false
    session.developerMode = false
    session.centralEnabled = false
  }
  session.loaded = true
}

const ensureSession = async () => {
  if (!session.loaded) await loadSession()
}

export const useSession = () => {
  return { session, loadSession, ensureSession }
}
