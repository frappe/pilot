import { ref } from 'vue'
import { settingsApi } from '@/api/settings'
import { parseBranchVersion } from '@/utils/format'

let cached = null

export function useBench() {
  const defaultBranch = ref(cached?.defaultBranch ?? '')
  const version = ref(cached?.version ?? '')

  async function load() {
    if (cached) return
    const settings = await settingsApi.get()
    const branch = settings.bench?.default_branch ?? ''
    cached = { defaultBranch: branch, version: parseBranchVersion(branch) }
    defaultBranch.value = cached.defaultBranch
    version.value = cached.version
  }

  return { defaultBranch, version, load }
}
