import { computed, ref } from 'vue'
import { toast } from 'frappe-ui'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { gitApi } from '@/api/git'
import type { GitConnection } from '@/types/git'
import { errorMessage } from '@/utils/error'

export const useGithubConnection = () => {
  const loading = ref(true)
  const connecting = ref(false)
  const verifying = ref(false)
  const redirecting = ref(false)
  const error = ref('')
  const status = ref<GitConnection | null>(null)
  const username = ref('')
  const token = ref('')

  const viaCentral = computed(() => status.value?.auth_type === 'github_app')
  const centralAvailable = computed(() => Boolean(status.value?.central?.available))
  const centralAccounts = computed(() => status.value?.central?.connections ?? [])
  const connected = computed(() =>
    Boolean(status.value?.connected && (viaCentral.value || status.value?.is_token_valid)),
  )
  const tokenHelpUrl = computed(
    () =>
      status.value?.providers?.github ||
      'https://github.com/settings/tokens/new?scopes=repo&description=Bench+CLI',
  )

  const load = async () => {
    loading.value = true

    try {
      status.value = await gitApi.status()

      if (status.value?.username && !viaCentral.value) username.value = status.value.username
    } finally {
      loading.value = false
    }
  }

  const connect = async () => {
    if (!token.value.trim()) {
      error.value = 'Paste a personal access token to connect.'

      return false
    }

    connecting.value = true
    error.value = ''

    try {
      const result = await gitApi.connect('github', token.value.trim(), username.value.trim())

      if (hasApiError(result)) {
        error.value = apiErrorMessage(result, 'Could not verify token.')

        return false
      }

      token.value = ''
      status.value = { ...result, central: status.value?.central }
      toast.success(`Connected as ${result.username}`)

      return true
    } catch (e) {
      error.value = errorMessage(e, 'Could not verify token.')

      return false
    } finally {
      connecting.value = false
    }
  }

  const connectViaCentral = async (returnUrl: string) => {
    redirecting.value = true
    error.value = ''

    try {
      const result = await gitApi.connectViaCentral(returnUrl)

      if (hasApiError(result))
        throw new Error(apiErrorMessage(result, 'Could not reach Frappe Cloud.'))

      window.location.assign(result.url)
    } catch (e) {
      error.value = errorMessage(e, 'Could not reach Frappe Cloud.')
      redirecting.value = false
    }
  }

  const verifyConnection = async () => {
    verifying.value = true

    try {
      const result = await gitApi.repos()

      if (Array.isArray(result)) toast.success('GitHub connection is working')
      else toast.error(apiErrorMessage(result, 'GitHub connection failed'))
    } catch (e) {
      toast.error(errorMessage(e, 'GitHub connection failed'))
    } finally {
      await load()
      verifying.value = false
    }
  }

  const disconnect = async () => {
    const response = await gitApi.disconnect()

    if (!response.ok) {
      toast.error(apiErrorMessage(await response.json(), 'Could not disconnect GitHub.'))

      return
    }

    username.value = ''
    await load()
  }

  return {
    loading,
    connecting,
    verifying,
    redirecting,
    error,
    status,
    username,
    token,
    connected,
    viaCentral,
    centralAvailable,
    centralAccounts,
    tokenHelpUrl,
    load,
    connect,
    connectViaCentral,
    verifyConnection,
    disconnect,
  }
}
