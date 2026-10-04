<script setup lang="ts">
import { Button, ErrorMessage, Select, Skeleton, TextInput } from 'frappe-ui'
import { computed, onMounted, reactive, ref } from 'vue'
import type { Store } from '../store'

interface Props {
  store: Store
}

const props = defineProps<Props>()
const emit = defineEmits(['close', 'saved'])
const store = props.store

const REQUIRED = ['currency', 'legal_name', 'address_line1', 'city', 'state', 'country', 'pincode']

const FIELDS = [
  { key: 'legal_name', label: __('Legal name') },
  {
    key: 'email',
    label: __('Billing email'),
    type: 'email',
    placeholder: 'billing@company.com',
  },
  {
    key: 'address_line1',
    label: __('Billing address'),
    placeholder: __('Street address'),
    full: true,
  },
  { key: 'city', label: __('City') },
  { key: 'state', label: __('State') },
  { key: 'country', label: __('Country') },
  { key: 'pincode', label: __('PIN / ZIP') },
  { key: 'gstin', label: __('GSTIN'), placeholder: '29ABCDE1234F1Z5' },
]

const form = reactive({
  currency: '',
  legal_name: '',
  email: '',
  address_line1: '',
  city: '',
  state: '',
  country: '',
  pincode: '',
  gstin: '',
})

const currencies = ref([])
const loaded = ref(false)
const working = ref(false)
const error = ref('')

const canSave = computed(
  () => !working.value && REQUIRED.every((key) => String(form[key] || '').trim()),
)

const noCurrencies = computed(() => loaded.value && !currencies.value.length)

const load = async () => {
  error.value = ''

  try {
    const profile = await store.api.getBillingProfile()

    currencies.value = (profile.supported_currencies || []).map((c) =>
      typeof c === 'string' ? { label: c, value: c } : c,
    )

    for (const key of Object.keys(form)) form[key] = profile[key] || ''

    loaded.value = true
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  }
}

onMounted(load)

const save = async () => {
  if (!canSave.value) return

  working.value = true
  error.value = ''

  try {
    await store.api.saveBillingProfile({ ...form })
    await store.loadBilling(true)

    emit('saved')
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  } finally {
    working.value = false
  }
}
</script>

<template>
  <section class="space-y-4 rounded-6 border border-outline-gray-2 p-5">
    <div>
      <h2 class="text-base-semibold text-ink-gray-8">
        {{ __("Add billing details") }}
      </h2>

      <p class="mt-1 text-p-sm text-ink-gray-5">
        {{ __("These are visible on every invoice and are needed to add a payment method.") }}
      </p>
    </div>

    <ErrorMessage :message="error" />

    <Skeleton v-if="!loaded && !error" class="h-20 rounded-6" />

    <div v-else-if="!loaded" class="flex gap-2">
      <Button @click="emit('close')">{{ __("Cancel") }}</Button>
      <Button variant="solid" @click="load">{{ __("Try again") }}</Button>
    </div>

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-2">
        <TextInput
          v-for="field in FIELDS"
          :key="field.key"
          v-model="form[field.key]"
          :type="field.type || 'text'"
          :label="
            REQUIRED.includes(field.key) ? `${field.label} *` : field.label
          "
          :placeholder="field.placeholder"
          :disabled="working"
          :class="field.full ? 'sm:col-span-2' : ''"
        />

        <Select
          v-model="form.currency"
          :label="`${__('Currency')} *`"
          :options="currencies"
          :disabled="working"
        />
      </div>

      <p v-if="noCurrencies" class="text-p-sm text-ink-amber-8">
        {{ __(
            "No billing currencies are configured, so this form can't be saved yet.",
          ) }}
      </p>

      <div class="flex justify-end gap-2">
        <Button :disabled="working" @click="emit('close')" :label="__('Cancel')" />

        <Button variant="solid" :loading="working" :disabled="!canSave" @click="save">
          {{ __("Save") }}
        </Button>
      </div>
    </template>
  </section>
</template>
