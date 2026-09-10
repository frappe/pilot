<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Alert, Button, ErrorMessage, Spinner, TextInput, toast } from 'frappe-ui'

import { apiErrorMessage } from '@/api/client'
import { gitApi } from '@/api/git'

const loading = ref(true)
const connecting = ref(false)
const verifying = ref(false)
const error = ref('')
const status = ref(null)
const username = ref('')
const token = ref('')

const connected = computed(() => Boolean(status.value?.connected && status.value?.is_token_valid))
const tokenHelpUrl = computed(
  () =>
    status.value?.providers?.github ||
    'https://github.com/settings/tokens/new?scopes=repo&description=Bench+CLI',
)

const load = async () => {
  loading.value = true
  try {
    status.value = await gitApi.status()
    if (status.value?.username) username.value = status.value.username
  } finally {
    loading.value = false
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
    if (result.error) {
      error.value = apiErrorMessage(result, 'Could not verify token.')
    } else {
      token.value = ''
      status.value = result
      toast.success(`Connected as ${result.username}`)
    }
  } catch (e) {
    error.value = e.message || 'Could not verify token.'
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
    toast.error(e.message || 'GitHub connection failed')
  } finally {
    await load()
    verifying.value = false
  }
}

const disconnect = async () => {
  await gitApi.disconnect()
  username.value = ''
  await load()
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <Alert
      v-if="!connected"
      theme="blue"
      title="Connect GitHub"
      :dismissible="false"
      class="border-outline-gray-2 border"
    >
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Install private apps and browse your repos. Paste a
          <a
            :href="tokenHelpUrl"
            target="_blank"
            rel="noopener"
            class="underline underline-offset-2"
            >token</a
          >
          with <code class="text-xs">repo</code> scope below.
        </p>
      </template>
    </Alert>

    <div
      v-if="connected"
      class="flex sm:flex-row sm:justify-between sm:items-center flex-col gap-3"
    >
      <div>
        <p class="font-medium text-ink-gray-8">Connected as {{ username }}</p>
        <p class="text-ink-gray-5 text-p-sm">GitHub · Personal access token</p>
      </div>

      <div class="flex items-center gap-2">
        <Button class="flex-1 sm:flex-none" :loading="verifying" @click="verifyConnection"
          >Verify</Button
        >
        <Button class="flex-1 sm:flex-none" theme="red" @click="disconnect">Disconnect</Button>
      </div>
    </div>

    <div class="space-y-4">
      <TextInput label="GitHub Username" v-model="username" placeholder="octocat" />
      <TextInput
        label="Personal Access Token"
        type="password"
        v-model="token"
        :placeholder="connected ? status.token_preview : 'ghp_…'"
        @keydown.enter="verifyAndConnect"
      />
      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end">
        <Button
          variant="solid"
          :loading="connecting"
          :disabled="!token.trim()"
          @click="verifyAndConnect"
        >
          {{ connected ? 'Update Token' : 'Verify & Connect' }}
        </Button>
      </div>
    </div>
  </div>
</template>
