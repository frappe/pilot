<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Button, Combobox, Dialog, ErrorMessage, Select, Textarea, TextInput } from 'frappe-ui'

import { errorMessage } from '@/utils/error'
import {
  castConfigValue,
  CONFIG_TYPES,
  type ConfigKey,
  type ConfigType,
  formatConfigValue,
  inferConfigType,
  SECRET_MASK,
} from '@/utils/siteConfig'
import siteConfigKeys from '@/utils/siteConfigKeys.json'

type Config = Record<string, unknown>

const CUSTOM_KEY = '__custom__'
const KNOWN_KEYS = siteConfigKeys as ConfigKey[]
const TYPE_OPTIONS = CONFIG_TYPES.map((type) => ({ label: type, value: type }))
// Whether a value is write-only follows from its key name, so a custom key cannot pick it.
const CUSTOM_TYPE_OPTIONS = TYPE_OPTIONS.filter((option) => option.value !== 'Password')
const BOOLEAN_OPTIONS = [
  { label: 'True', value: 'true' },
  { label: 'False', value: 'false' },
]

const props = defineProps<{
  config: Config
  editKey: string | null
  hiddenKeys: string[]
  save: (patch: Config) => Promise<unknown>
  reload: () => Promise<unknown>
}>()

const open = defineModel<boolean>({ required: true })

const choice = ref('')
const key = ref('')
const customType = ref<ConfigType>('String')
const value = ref('')
const saving = ref(false)
const error = ref('')

const isNew = computed(() => props.editKey === null)
const known = computed(() => KNOWN_KEYS.find((item) => item.key === key.value.trim()))
const isSecret = computed(() => !isNew.value && props.config[props.editKey ?? ''] === SECRET_MASK)
const isTypeFixed = computed(() => Boolean(known.value) || isSecret.value)
const type = computed(() => known.value?.type ?? (isSecret.value ? 'Password' : customType.value))
const inputType = computed(() => {
  if (type.value === 'Number') return 'number'
  return type.value === 'Password' ? 'password' : 'text'
})

const keyOptions = computed(() => [
  ...KNOWN_KEYS.filter(
    (item) => !(item.key in props.config) && !props.hiddenKeys.includes(item.key),
  ).map((item) => ({
    label: item.title,
    value: item.key,
    description: item.key,
  })),
  { label: 'Custom key', value: CUSTOM_KEY, icon: 'lucide-plus' },
])

const reset = () => {
  error.value = ''
  key.value = props.editKey ?? ''
  if (props.editKey === null) {
    choice.value = ''
    customType.value = 'String'
    value.value = ''
    return
  }
  const current = props.config[props.editKey]
  customType.value = inferConfigType(current)
  choice.value = known.value ? props.editKey : CUSTOM_KEY
  value.value = isSecret.value ? '' : formatConfigValue(current, type.value)
}

watch(open, (isOpen) => isOpen && reset())

const choose = (selected: unknown) => {
  choice.value = String(selected ?? '')
  key.value = choice.value === CUSTOM_KEY ? '' : choice.value
  customType.value = 'String'
  value.value = ''
  error.value = ''
}

const submit = async () => {
  const name = key.value.trim()
  if (!name) {
    error.value = 'Key is required.'
    return
  }
  if (isNew.value && name in props.config) {
    error.value = 'Key already exists.'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await props.save({ [name]: castConfigValue(String(value.value), type.value) })
    await props.reload()
    open.value = false
  } catch (e) {
    error.value = errorMessage(e, 'Failed to save.')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog v-model="open" :title="isNew ? 'Add config' : `Edit ${known?.title ?? editKey}`" size="xl">
    <div class="space-y-3">
      <Combobox
        v-if="isNew"
        label="Config name"
        :options="keyOptions"
        :model-value="choice"
        placeholder="Search config keys…"
        @update:model-value="choose"
      />

      <template v-if="choice">
        <TextInput
          label="Key"
          v-model="key"
          placeholder="config_key"
          :disabled="choice !== CUSTOM_KEY || !isNew"
          class="w-full"
        />
        <Select
          label="Type"
          :model-value="type"
          :options="isTypeFixed ? TYPE_OPTIONS : CUSTOM_TYPE_OPTIONS"
          :disabled="isTypeFixed"
          @update:model-value="(selected) => (customType = selected as ConfigType)"
        />
        <Select
          v-if="type === 'Boolean'"
          label="Value"
          v-model="value"
          :options="BOOLEAN_OPTIONS"
          placeholder="Choose a value"
        />
        <Textarea
          v-else-if="type === 'JSON'"
          label="Value"
          v-model="value"
          :rows="4"
          placeholder='{"key": "value"}'
        />
        <TextInput
          v-else
          label="Value"
          v-model="value"
          :type="inputType"
          :placeholder="type === 'Password' ? 'Enter a new value' : ''"
          class="w-full"
        />
        <p v-if="known?.description" class="text-ink-gray-5 text-p-sm">{{ known.description }}</p>
        <p v-if="type === 'Password'" class="text-ink-gray-5 text-p-sm">
          Write-only: the saved value is never shown.
        </p>
      </template>

      <ErrorMessage v-if="error" :message="error" />
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="open = false">Cancel</Button>
        <Button variant="solid" :loading="saving" :disabled="!choice" @click="submit">Save</Button>
      </div>
    </template>
  </Dialog>
</template>
