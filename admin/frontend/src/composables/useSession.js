import { reactive } from 'vue'
import { authApi } from '../api/auth'

const session = reactive({
  loaded: false,
  authenticated: false,
  wizard: false,
  enabled: true,
  benchName: '',
  allowBenchManagement: true,
})

async function loadSession() {
  try {
    const status = await authApi.status()
    session.authenticated = status.authenticated !== false
    session.wizard = status.wizard === true
    session.enabled = status.enabled !== false
    session.benchName = status.name || ''
    session.allowBenchManagement = status.allow_bench_management !== false
  } catch {
    session.authenticated = false
  }
  session.loaded = true
}

async function ensureSession() {
  if (!session.loaded) await loadSession()
}

export function useSession() {
  return { session, loadSession, ensureSession }
}
