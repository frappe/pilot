import { ref } from 'vue'
import { settingsApi } from '@/api/settings'
import { parseBranchVersion } from '@/utils/format'

let cached = null

export function useBench() {
  const name = ref(cached?.name ?? '')
  const defaultBranch = ref(cached?.defaultBranch ?? '')
  const version = ref(cached?.version ?? '')

  async function load() {
    if (cached) return
    const settings = await settingsApi.get()
    const branch = settings.bench?.default_branch ?? ''
    cached = { name: settings.bench?.name || 'this bench', defaultBranch: branch, version: parseBranchVersion(branch) }
    name.value = cached.name
    defaultBranch.value = cached.defaultBranch
    version.value = cached.version
  }

  return { name, defaultBranch, version, load }
}
