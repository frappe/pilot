import { reactive } from 'vue'
import { authApi } from '../../api/auth'

const session = reactive({
  loaded: false,
  authenticated: false,
  wizard: false,
  enabled: false,
  benchName: '',
  allowBenchManagement: false,
  developerMode: false,
})

async function loadSession() {
  try {
    const [bootstrap, currentSession] = await Promise.all([authApi.bootstrap(), authApi.session()])
    session.authenticated = currentSession.authenticated === true
    session.wizard = bootstrap.mode === 'setup'
    session.enabled = bootstrap.enabled === true
    session.benchName = bootstrap.name || ''
    session.allowBenchManagement = bootstrap.allow_bench_management === true
    session.developerMode = bootstrap.developer_mode === true
  } catch {
    session.authenticated = false
    session.wizard = false
    session.enabled = false
    session.benchName = ''
    session.allowBenchManagement = false
    session.developerMode = false
  }
  session.loaded = true
}

async function ensureSession() {
  if (!session.loaded) await loadSession()
}

export function useSession() {
  return { session, loadSession, ensureSession }
}
