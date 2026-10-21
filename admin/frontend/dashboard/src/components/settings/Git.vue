<script setup lang="ts">
import {
  Alert,
  Button,
  Dropdown,
  type DropdownItem,
  ErrorMessage,
  Spinner,
  TextInput,
  toast,
} from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { gitApi } from '@/api/git'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import { GENERAL_SECTIONS } from '@/components/settings/sections'
import type { GitConnection } from '@/types/git'
import { errorMessage } from '@/utils/error'

const openSection = defineModel<{ id: string } | null>('openSection')
const route = useRoute()
const router = useRouter()

const loading = ref(true)
const connecting = ref(false)
const redirecting = ref(false)
const verifying = ref(false)
const disconnecting = ref(false)
const error = ref('')
const status = ref<GitConnection | null>(null)
const username = ref('')
const token = ref('')

const viaCentral = computed(() => status.value?.auth_type === 'github_app')
const connected = computed(() =>
  Boolean(status.value?.connected && (viaCentral.value || status.value?.is_token_valid)),
)
const centralAvailable = computed(() => Boolean(status.value?.central?.available))
const centralError = computed(() => status.value?.central?.error ?? '')
const centralAccounts = computed(() => status.value?.central?.connections ?? [])
const showTokenForm = computed(() => openSection.value?.id === 'github-token')
const openTokenScreen = (id: 'github' | 'github-token') => {
  openSection.value = GENERAL_SECTIONS.find((section) => section.id === id) ?? null
}
const tokenFormVisible = computed(
  () => !viaCentral.value && (!centralAvailable.value || showTokenForm.value || connected.value),
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

const connectViaCentral = async () => {
  redirecting.value = true
  error.value = ''
  try {
    const returnUrl = `${window.location.origin}${route.path}?github=connected`
    const result = await gitApi.connectViaCentral(returnUrl)
    if (hasApiError(result))
      throw new Error(apiErrorMessage(result, 'Could not reach Frappe Cloud.'))
    window.location.assign(result.url)
  } catch (e) {
    error.value = errorMessage(e, 'Could not reach Frappe Cloud.')
    redirecting.value = false
  }
}

const verifyAndConnect = async () => {
  if (!token.value.trim()) {
    error.value = 'Paste a personal access token to connect.'
    return
  }
  connecting.value = true
  error.value = ''
  try {
    const result = await gitApi.connect('github', token.value.trim(), username.value.trim())
    if (hasApiError(result)) {
      error.value = apiErrorMessage(result, 'Could not verify token.')
    } else {
      token.value = ''
      status.value = { ...result, central: status.value?.central }
      toast.success(`Connected as ${result.username}`)
      if (showTokenForm.value) openTokenScreen('github')
    }
  } catch (e) {
    error.value = errorMessage(e, 'Could not verify token.')
  } finally {
    connecting.value = false
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
  disconnecting.value = true
  try {
    const response = await gitApi.disconnect()
    if (!response.ok) {
      toast.error(apiErrorMessage(await response.json(), 'Could not disconnect GitHub.'))
      return
    }
    username.value = ''
    await load()
  } finally {
    disconnecting.value = false
  }
}

const centralActions: DropdownItem[] = [
  { label: 'Verify connection', icon: 'lucide-circle-check', onClick: () => verifyConnection() },
  { label: 'Manage repositories', icon: 'lucide-settings-2', onClick: () => connectViaCentral() },
  { label: 'Disconnect', icon: 'lucide-unplug', theme: 'red', onClick: () => disconnect() },
]
const tokenActions: DropdownItem[] = [centralActions[0], centralActions[2]]

onMounted(async () => {
  await load()
  if (route.query.github === 'connected') {
    if (viaCentral.value) toast.success('GitHub connected through Frappe Cloud')
    router.replace({ query: { ...route.query, github: undefined } })
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else-if="viaCentral" class="space-y-6">
    <div class="flex justify-between items-start gap-3">
      <div>
        <p class="font-medium text-ink-gray-8">Connected through Frappe Cloud</p>
        <p class="text-ink-gray-5 text-p-sm">Read-only access to these repositories.</p>
      </div>
      <Dropdown :options="centralActions" align="end">
        <template #default="{ open }">
          <Button
            :active="open"
            :loading="verifying || redirecting || disconnecting"
            icon="lucide-ellipsis"
            label="GitHub actions"
            tooltip="Actions"
          />
        </template>
      </Dropdown>
    </div>

    <div v-for="account in centralAccounts" :key="account.account" class="space-y-1">
      <p class="text-sm font-medium text-ink-gray-5">{{ account.account }}</p>
      <p v-if="!account.repositories.length" class="py-1.5 text-p-sm text-ink-gray-5">
        No repositories granted.
      </p>
      <div
        v-for="repository in account.repositories"
        :key="repository"
        class="flex items-center gap-2 py-1.5 text-base text-ink-gray-8"
      >
        <span class="size-4 text-ink-gray-5 lucide-book-marked" aria-hidden="true" />
        {{ repository.slice(account.account.length + 1) }}
      </div>
    </div>
    <ErrorMessage v-if="error" :message="error" />
  </div>

  <div v-else class="space-y-6">
    <Alert
      v-if="centralError"
      theme="amber"
      title="Frappe Cloud is not reachable"
      :dismissible="false"
    >
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">A personal access token still works.</p>
      </template>
    </Alert>

    <Alert
      v-if="!connected && !showTokenForm"
      theme="blue"
      title="Connect GitHub"
      :dismissible="false"
      class="border-outline-gray-2 border"
    >
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">Install private apps and browse your repos.</p>
      </template>
    </Alert>

    <div v-if="connected" class="flex justify-between items-start gap-3">
      <div>
        <p class="font-medium text-ink-gray-8">Connected as {{ username }}</p>
        <p class="text-ink-gray-5 text-p-sm">Personal access token</p>
      </div>
      <Dropdown :options="tokenActions" align="end">
        <template #default="{ open }">
          <Button
            :active="open"
            :loading="verifying || disconnecting"
            icon="lucide-ellipsis"
            label="GitHub actions"
            tooltip="Actions"
          />
        </template>
      </Dropdown>
    </div>

    <div
      v-if="centralAvailable && !showTokenForm"
      class="-mx-2.5 divide-y divide-outline-alpha-gray-1 hover-merges-dividers"
    >
      <SettingsRow
        label="Connect through Frappe Cloud"
        :description="
          connected
            ? 'Replace the token with read-only access to the repositories you choose.'
            : 'Connect GitHub repositories easily.'
        "
      >
        <Button :loading="redirecting" @click="connectViaCentral">
          {{ connected ? 'Switch' : 'Connect' }}
        </Button>
      </SettingsRow>
      <SettingsRow
        v-if="!connected"
        label="Use a personal access token"
        description="Store your own GitHub token on this server."
      >
        <Button @click="openTokenScreen('github-token')">Connect</Button>
      </SettingsRow>
    </div>

    <div v-if="tokenFormVisible" class="space-y-4">
      <TextInput label="GitHub username" v-model="username" placeholder="octocat" />
      <div class="space-y-1.5">
        <TextInput
          label="Personal access token"
          type="password"
          v-model="token"
          :placeholder="connected ? status?.token_preview : 'ghp_…'"
          @keydown.enter="verifyAndConnect"
        />
        <a
          :href="tokenHelpUrl"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-0.5 text-p-sm text-ink-gray-5 hover:text-ink-gray-8"
        >
          Create a token with repo scope
          <span class="size-3 lucide-arrow-up-right" aria-hidden="true" />
        </a>
      </div>
      <div class="flex justify-end gap-2">
        <Button :loading="connecting" :disabled="!token.trim()" @click="verifyAndConnect">
          {{ connected ? 'Update token' : 'Connect' }}
        </Button>
      </div>
    </div>
    <ErrorMessage v-if="error" :message="error" />
  </div>
</template>
