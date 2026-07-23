<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Alert v-if="!connected" theme="blue" title="Why connect an AI assistant?" :dismissible="false">
      <template #description>
        <p class="text-ink-gray-6 text-p-sm">
          Connect any LLM provider supported by litellm to power assistant features, like explaining
          migration and task errors from the logs.
        </p>
      </template>
    </Alert>

    <div
      v-if="connected"
      class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3"
    >
      <div>
        <p class="font-medium text-ink-gray-8 text-sm">Connected to {{ providerLabel }}</p>
        <p class="text-ink-gray-5 text-p-sm">Model {{ model || '—' }} · API key set</p>
      </div>
      <Button
        class="flex-1 sm:flex-none"
        variant="subtle"
        theme="red"
        :loading="disconnecting"
        @click="disconnect"
        >Disconnect</Button
      >
    </div>

    <div class="space-y-4">
      <Autocomplete
        label="Provider"
        :options="providerOptions"
        :model-value="providerSelection"
        placeholder="Search providers…"
        @update:model-value="onProviderSelect"
      />

      <FormControl
        v-if="freeTextModel"
        label="Model"
        type="text"
        v-model="model"
        placeholder="Your served model name"
      />
      <Autocomplete
        v-else
        label="Model"
        :options="modelOptions"
        :model-value="modelSelection"
        :loading="modelsLoading"
        :placeholder="provider ? 'Search models…' : 'Select a provider first'"
        @update:model-value="(o) => (model = o?.value || '')"
      />

      <FormControl
        v-if="needsApiBase"
        label="API Base URL"
        type="text"
        v-model="apiBase"
        placeholder="http://your-host:8000/v1"
      />
      <FormControl
        label="API Key"
        type="password"
        v-model="apiKey"
        :placeholder="apiKeySet ? '••••••••' : 'Provider API key'"
      />
      <FormControl
        label="System Prompt"
        type="textarea"
        v-model="systemPrompt"
        :rows="6"
        placeholder="Instructions sent with every request"
      />

      <details class="group">
        <summary
          class="flex items-center gap-1.5 text-ink-gray-6 text-sm cursor-pointer select-none"
        >
          <span
            class="size-4 transition-transform group-open:rotate-90 lucide-chevron-right"
          ></span>
          Advanced
        </summary>
        <div class="space-y-4 pt-4">
          <FormControl
            v-if="!needsApiBase"
            label="API Base URL"
            type="text"
            v-model="apiBase"
            placeholder="Leave blank to use the provider default"
          />
          <FormControl label="Max Tokens" type="number" v-model="maxTokens" placeholder="4096" />
        </div>
      </details>

      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end">
        <Button variant="solid" :loading="saving" @click="save">
          {{ connected ? 'Update' : 'Connect' }}
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Alert, Autocomplete, Button, ErrorMessage, FormControl, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'

const loading = ref(true)
const saving = ref(false)
const disconnecting = ref(false)
const modelsLoading = ref(false)
const error = ref('')
const provider = ref('')
const model = ref('')
const apiKey = ref('')
const maxTokens = ref(4096)
const apiBase = ref('')
const systemPrompt = ref('')
const apiKeySet = ref(false)
const providers = ref([])
const models = ref([])

const connected = computed(() => Boolean(provider.value && apiKeySet.value))
const selectedProvider = computed(() => providers.value.find((p) => p.value === provider.value))
const providerLabel = computed(() => selectedProvider.value?.label || provider.value)
const needsApiBase = computed(() => Boolean(selectedProvider.value?.requires_api_base))
const freeTextModel = computed(() => Boolean(selectedProvider.value?.free_text_model))

const providerOptions = computed(() =>
  providers.value.map((p) => ({ label: p.label, value: p.value })),
)
const providerSelection = computed(
  () => providerOptions.value.find((o) => o.value === provider.value) || null,
)
const modelOptions = computed(() => models.value.map((m) => ({ label: m, value: m })))
const modelSelection = computed(() =>
  model.value ? { label: model.value, value: model.value } : null,
)

async function fetchModels(providerValue) {
  models.value = []
  if (!providerValue || freeTextModel.value) return
  modelsLoading.value = true
  try {
    models.value = (await settingsApi.llmModels(providerValue)) || []
  } finally {
    modelsLoading.value = false
  }
}

function onProviderSelect(option) {
  provider.value = option?.value || ''
  model.value = ''
  fetchModels(provider.value)
}

async function load() {
  loading.value = true
  try {
    const data = await settingsApi.get()
    providers.value = data.llm_providers || []
    const llm = data.llm || {}
    provider.value = llm.provider || ''
    model.value = llm.model || ''
    maxTokens.value = llm.max_tokens || 4096
    systemPrompt.value = llm.system_prompt || ''
    apiBase.value = llm.api_base || ''
    apiKeySet.value = !!llm.api_key_set
    if (provider.value) await fetchModels(provider.value)
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!provider.value) {
    error.value = 'Provider is required.'
    return
  }
  if (!model.value.trim()) {
    error.value = 'Model is required.'
    return
  }
  if (needsApiBase.value && !apiBase.value.trim()) {
    error.value = 'API base URL is required for a self-hosted provider.'
    return
  }
  if (!apiKeySet.value && !apiKey.value.trim()) {
    error.value = 'API key is required.'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const result = await settingsApi.update({
      llm: {
        provider: provider.value,
        api_key: apiKey.value.trim(),
        model: model.value.trim(),
        max_tokens: Number(maxTokens.value) || 4096,
        api_base: apiBase.value.trim(),
        system_prompt: systemPrompt.value,
      },
    })
    if (!result.error) {
      apiKey.value = ''
      toast.success('AI assistant settings saved')
      await load()
    } else {
      error.value = apiErrorMessage(result, 'Could not save AI assistant settings.')
    }
  } catch (e) {
    error.value = e.message || 'Could not save AI assistant settings.'
  } finally {
    saving.value = false
  }
}

async function disconnect() {
  disconnecting.value = true
  try {
    const result = await settingsApi.update({ llm: { disconnect: true } })
    if (!result.error) {
      provider.value = ''
      model.value = ''
      apiKey.value = ''
      maxTokens.value = 4096
      apiBase.value = ''
      systemPrompt.value = ''
      apiKeySet.value = false
      models.value = []
      toast.success('AI assistant disconnected')
    } else {
      toast.error(apiErrorMessage(result, 'Could not disconnect the AI assistant.'))
    }
  } catch (e) {
    toast.error(e.message || 'Could not disconnect the AI assistant.')
  } finally {
    disconnecting.value = false
  }
}

onMounted(load)
</script>
