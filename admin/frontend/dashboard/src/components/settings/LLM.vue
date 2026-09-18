<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Alert, Button, Combobox, ErrorMessage, Spinner, TextInput, Textarea, toast } from 'frappe-ui'

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
const modelsError = ref('')

const connected = computed(() => Boolean(provider.value && apiKeySet.value))
const selectedProvider = computed(() => providers.value.find((p) => p.value === provider.value))
const providerLabel = computed(() => selectedProvider.value?.label || provider.value)
const needsApiBase = computed(() => Boolean(selectedProvider.value?.requires_api_base))
const freeTextModel = computed(() => Boolean(selectedProvider.value?.free_text_model))
const modelsNeedApiKey = computed(() => Boolean(selectedProvider.value?.models_need_api_key))
const hasApiKey = computed(() => Boolean(apiKey.value.trim() || apiKeySet.value))

const providerOptions = computed(() =>
  providers.value.map((p) => ({ label: p.label, value: p.value })),
)
const modelOptions = computed(() => models.value.map((m) => ({ label: m, value: m })))
const hasApiBase = computed(() => Boolean(apiBase.value.trim()))
const apiBaseError = computed(() => {
  if (!provider.value || !needsApiBase.value || hasApiBase.value) return ''
  return `${providerLabel.value} needs the endpoint of your server, e.g. http://your-host:8000/v1`
})

// Connect stays dead until every required field is filled.
const canSave = computed(
  () =>
    Boolean(provider.value) &&
    Boolean(model.value.trim()) &&
    hasApiKey.value &&
    (!needsApiBase.value || hasApiBase.value),
)

const modelPlaceholder = computed(() => {
  if (!provider.value) return 'Select a provider first'
  if (needsApiBase.value && !hasApiBase.value) return 'Enter the API base URL to load models'
  if (!models.value.length && !hasApiKey.value) return 'Enter the API key to load models'
  return 'Search models…'
})
// An empty picklist with no key entered is a missing key - say so.
const modelsHint = computed(() => {
  if (!provider.value || modelsLoading.value || models.value.length) return ''
  if (needsApiBase.value && !hasApiBase.value) return ''
  if (hasApiKey.value) return ''
  return `Enter the ${providerLabel.value} API key above to load models.`
})

const fetchModels = async (providerValue) => {
  models.value = []
  modelsError.value = ''
  if (!providerValue || freeTextModel.value) return
  // The key is sent along; the backend falls back to the saved one.
  if (modelsNeedApiKey.value && !hasApiKey.value) return
  if (needsApiBase.value && !hasApiBase.value) return
  modelsLoading.value = true
  try {
    const result = await settingsApi.llmModels(providerValue, apiKey.value.trim(), apiBase.value.trim())
    if (result?.error) modelsError.value = apiErrorMessage(result, 'Could not load models.')
    else models.value = result || []
  } catch (e) {
    modelsError.value = e.message || 'Could not load models.'
  } finally {
    modelsLoading.value = false
  }
}

const onProviderSelect = (value) => {
  provider.value = value || ''
  model.value = ''
  fetchModels(provider.value)
}

// A key-gated provider can only list models once a key exists, so reload when it settles.
let apiKeyDebounce = null
watch([apiKey, apiBase], () => {
  if (!modelsNeedApiKey.value || !provider.value) return
  clearTimeout(apiKeyDebounce)
  apiKeyDebounce = setTimeout(() => fetchModels(provider.value), 600)
})

const load = async () => {
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

const save = async () => {
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

const disconnect = async () => {
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
      modelsError.value = ''
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

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <Alert
      v-if="!connected"
      class="border border-outline-gray-2"
      theme="blue"
      title="Why connect an AI assistant?"
      :dismissible="false"
    >
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
        <p class="font-medium text-ink-gray-8">Connected to {{ providerLabel }}</p>
        <p class="text-ink-gray-5 text-p-sm">Model {{ model || '—' }} · API key set</p>
      </div>

      <Button
        theme="red"
        :loading="disconnecting"
        @click="disconnect"
        >Disconnect</Button
      >
    </div>

    <div class="space-y-4">
      <Combobox
        label="Provider"
        :options="providerOptions"
        :model-value="provider"
        placeholder="Search providers…"
        @update:model-value="onProviderSelect"
      />

      <div v-if="needsApiBase" class="space-y-1.5">
        <TextInput
          label="API Base URL"
          v-model="apiBase"
          placeholder="http://your-host:8000/v1"
        />
        <p v-if="apiBaseError" class="text-ink-red-5 text-p-sm">{{ apiBaseError }}</p>
      </div>

      <TextInput
        label="API Key"
        type="password"
        v-model="apiKey"
        :placeholder="apiKeySet ? '••••••••' : 'Provider API key'"
      />

      <TextInput
        v-if="freeTextModel"
        label="Model"
        v-model="model"
        placeholder="Your served model name"
      />
      <div v-else class="space-y-1.5">
        <Combobox
          label="Model"
          :options="modelOptions"
          :model-value="model"
          :loading="modelsLoading"
          :placeholder="modelPlaceholder"
          @update:model-value="(value) => (model = value || '')"
        />
        <p v-if="modelsError" class="text-ink-red-5 text-p-sm">{{ modelsError }}</p>
        <p v-else-if="modelsHint" class="text-ink-gray-5 text-p-sm">{{ modelsHint }}</p>
      </div>

      <Textarea
        label="System Prompt"
        v-model="systemPrompt"
        :rows="6"
        placeholder="Instructions sent with every request"
      />

      <details class="group">
        <summary
          class="flex items-center gap-1.5 text-ink-gray-6 cursor-pointer select-none"
        >
          <span
            class="size-4 transition-transform group-open:rotate-90 lucide-chevron-right" />
          Advanced
        </summary>

        <div class="space-y-4 pt-4">
          <TextInput
            v-if="!needsApiBase"
            label="API Base URL"
            v-model="apiBase"
            placeholder="Leave blank to use the provider default"
          />
          <TextInput label="Max Tokens" type="number" v-model="maxTokens" placeholder="4096" />
        </div>
      </details>

      <ErrorMessage v-if="error" :message="error" />
      <div class="flex justify-end">
        <Button variant="solid" :loading="saving" :disabled="!canSave" @click="save">
          {{ connected ? 'Update' : 'Connect' }}
        </Button>
      </div>
    </div>
  </div>
</template>
