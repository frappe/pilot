<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Alert v-if="!connected" theme="blue" title="Why connect GitHub?" :dismissible="false">
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Connecting a GitHub account lets you install private apps and browse your repositories
          from the "Add app from GitHub" dialog. Generate a
          <a
            :href="tokenHelpUrl"
            target="_blank"
            rel="noopener"
            class="underline underline-offset-2"
          >
            personal access token</a
          >
          with <code class="text-xs">repo</code> scope, then paste it below.
        </p>
      </template>
    </Alert>

    <div
      v-if="connected"
      class="flex sm:flex-row sm:justify-between sm:items-center flex-col gap-3"
    >
      <div>
        <p class="font-medium text-ink-gray-8 text-sm">Connected as {{ username }}</p>
        <p class="text-ink-gray-5 text-p-sm">GitHub · Personal access token</p>
      </div>
      <div class="flex items-center gap-2">
        <Button
          class="flex-1 sm:flex-none"
          variant="subtle"
          :loading="verifying"
          @click="verifyConnection"
          >Verify</Button
        >
        <Button class="flex-1 sm:flex-none" variant="subtle" theme="red" @click="disconnect"
          >Disconnect</Button
        >
      </div>
    </div>

    <div class="space-y-4">
      <FormControl label="GitHub Username" type="text" v-model="username" placeholder="octocat" />
      <FormControl
        label="Personal Access Token"
        type="password"
        v-model="token"
        :placeholder="connected ? status.token_preview : 'ghp_…'"
        @keydown.enter="verifyAndConnect"
      />
      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end">
        <Button variant="solid" :loading="connecting" @click="verifyAndConnect">
          {{ connected ? 'Update Token' : 'Verify & Connect' }}
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, toast } from 'frappe-ui'
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

async function load() {
  loading.value = true
  try {
    status.value = await gitApi.status()
    if (status.value?.username) username.value = status.value.username
  } finally {
    loading.value = false
  }
}

async function verifyAndConnect() {
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

async function verifyConnection() {
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

async function disconnect() {
  await gitApi.disconnect()
  username.value = ''
  await load()
}

onMounted(load)
</script>
