<script setup lang="ts">
import { useTranslation } from '../translation'
import { getBillingProfile, saveBillingProfile } from '@frappe/cloud-sdk/api'
import { Button, Combobox, ErrorMessage, Skeleton, TextInput } from 'frappe-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useErrorMessage, type Store } from '../store'
import PhoneInput from './PhoneInput.vue'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
  /** Where this form sits in the payment steps, such as "Step 1 of 2". */
  step?: string
}

const props = defineProps<Props>()
const emit = defineEmits(['close', 'saved'])
const store = props.store

const form = reactive({
  currency: '',
  legal_name: '',
  email: '',
  phone: '',
  address_line1: '',
  city: '',
  state: '',
  country: '',
  pincode: '',
  gstin: '',
})

type Field = keyof typeof form

// Central needs these to invoice and to pick the payment gateway. India also needs the state for GST,
// and a phone, because Razorpay needs one to save a card. Central compares the country name exactly.
const isIndia = computed(() => form.country.trim() === 'India')
const required = computed<Field[]>(() => [
  'legal_name',
  'address_line1',
  'city',
  'country',
  ...(isIndia.value ? (['state', 'phone'] as Field[]) : []),
])

const FIELDS = computed(() => [
  { key: 'legal_name' as Field, label: __('Legal name'), autocomplete: 'organization', placeholder: __('Enter legal name') },
  { key: 'email' as Field, label: __('Billing email'), type: 'email', autocomplete: 'email', placeholder: __('Enter billing email') },
  { key: 'address_line1' as Field, label: __('Address'), autocomplete: 'address-line1', placeholder: __('Enter street address'), full: true },
  { key: 'city' as Field, label: __('City'), autocomplete: 'address-level2', placeholder: __('Enter city') },
  { key: 'country' as Field, label: __('Country'), autocomplete: 'country-name', placeholder: __('Choose country'), options: countries.value },
  // Only India's state is checked (for GST), so other countries keep a free-text state.
  isIndia.value && indiaStates.value.length
    ? { key: 'state' as Field, label: __('State'), autocomplete: 'address-level1', placeholder: __('Choose state'), options: indiaStates.value }
    : { key: 'state' as Field, label: __('State'), autocomplete: 'address-level1', placeholder: __('Enter state') },
  { key: 'pincode' as Field, label: __('PIN or ZIP code'), autocomplete: 'postal-code', placeholder: __('Enter PIN or ZIP code') },
  ...(isIndia.value ? [{ key: 'gstin' as Field, label: __('GSTIN'), placeholder: __('Enter GSTIN') }] : []),
])

const currencies = ref<string[]>([])
const isCurrencyLocked = ref(false)
const countries = ref<string[]>([])
const indiaStates = ref<string[]>([])
const loaded = ref(false)
const working = ref(false)
const error = ref('')
const missing = ref<Field[]>([])

// Central bills India in INR and every other country in USD, and keeps a currency once money moves in it.
const currency = computed(() => {
  if (isCurrencyLocked.value) return form.currency
  return isIndia.value ? 'INR' : 'USD'
})
const isCurrencyUnsupported = computed(() => loaded.value && !currencies.value.includes(currency.value))

// An Indian state is valid only for India, and India accepts only a listed state.
watch(() => form.country, () => {
  if (indiaStates.value.length && indiaStates.value.includes(form.state) !== isIndia.value) form.state = ''
})

const MISSING: Partial<Record<Field, string>> = {
  legal_name: __('Enter the name to show on invoices.'),
  address_line1: __('Enter your street address.'),
  city: __('Enter your city.'),
  state: __('Choose your state. GST needs it.'),
  country: __('Choose your country.'),
  phone: __('Enter a phone number. Razorpay needs it to save a card.'),
}

const fieldError = (key: Field) => (missing.value.includes(key) ? MISSING[key] || '' : '')

const load = async () => {
  error.value = ''

  try {
    const profile = await getBillingProfile()

    currencies.value = (profile.supported_currencies || []).map((option) => (typeof option === 'string' ? option : option.value))
    isCurrencyLocked.value = Boolean(profile.currency_locked)
    countries.value = profile.countries || []
    indiaStates.value = profile.india_states || []

    for (const key of Object.keys(form) as Field[]) form[key] = profile[key] || ''

    loaded.value = true
  } catch (exception) {
    error.value = getErrorMessage(exception)
  }
}

onMounted(load)

const save = async () => {
  if (working.value) return

  missing.value = required.value.filter((key) => !String(form[key] || '').trim())
  if (missing.value.length || isCurrencyUnsupported.value) return

  working.value = true
  error.value = ''

  try {
    await saveBillingProfile({ ...form, currency: currency.value, gstin: isIndia.value ? form.gstin : '' })
    await store.loadBilling(true)

    emit('saved')
  } catch (exception) {
    error.value = getErrorMessage(exception)
  } finally {
    working.value = false
  }
}
</script>

<template>
  <section>
    <p v-if="step" class="text-p-sm text-ink-gray-5">{{ step }}</p>

    <h3 class="text-base-semibold text-ink-gray-8" :class="step && 'mt-1'">{{ __("Billing details") }}</h3>

    <p class="mt-1 text-p-sm text-ink-gray-5">
      {{ __("Shown on your invoices.") }}
    </p>

    <ErrorMessage :message="error" class="mt-4" />

    <div
      v-if="!loaded && !error"
      class="mt-4 grid gap-4 sm:grid-cols-2"
      aria-busy="true"
      :aria-label="__('Loading billing details')"
    >
      <div v-for="field in [...FIELDS, { key: 'phone' }]" :key="field.key" :class="field.full && 'sm:col-span-2'">
        <Skeleton class="h-4 w-24 rounded-4" />
        <Skeleton class="mt-1.5 h-7 rounded-4" />
      </div>
    </div>

    <div v-else-if="!loaded" class="mt-4 flex justify-end gap-2">
      <Button :label="__('Cancel')" @click="emit('close')" />
      <Button variant="solid" :label="__('Try again')" @click="load" />
    </div>

    <form v-else class="mt-4" novalidate @submit.prevent="save">
      <div class="grid gap-4 sm:grid-cols-2">
        <div v-for="field in FIELDS" :key="field.key" :class="field.full && 'sm:col-span-2'">
          <Combobox
            v-if="field.options?.length"
            trigger="button"
            :model-value="form[field.key] || null"
            :label="field.label"
            :required="required.includes(field.key)"
            :placeholder="field.placeholder"
            :options="field.options"
            :disabled="working"
            :aria-invalid="Boolean(fieldError(field.key)) || undefined"
            @update:model-value="(value) => (form[field.key] = String(value || ''))"
          />
          <TextInput
            v-else
            v-model="form[field.key]"
            :type="field.type || 'text'"
            :label="field.label"
            :required="required.includes(field.key)"
            :placeholder="field.placeholder"
            :name="field.key"
            :autocomplete="field.autocomplete"
            :disabled="working"
            :aria-invalid="Boolean(fieldError(field.key)) || undefined"
          />
          <ErrorMessage :message="fieldError(field.key)" class="mt-1" />
        </div>

        <PhoneInput
          v-model="form.phone"
          :label="__('Phone')"
          :country="form.country"
          :required="required.includes('phone')"
          :disabled="working"
          :error="fieldError('phone')"
        />
      </div>

      <p v-if="isCurrencyUnsupported" class="mt-4 text-p-sm text-ink-amber-8">
        {{ __("Billing in {0} is not set up yet. Contact support to add a payment method.", [currency]) }}
      </p>

      <div class="mt-5 flex justify-end gap-2">
        <Button :disabled="working" :label="__('Cancel')" @click="emit('close')" />
        <Button type="submit" variant="solid" :loading="working" :label="__('Continue')" />
      </div>
    </form>
  </section>
</template>
