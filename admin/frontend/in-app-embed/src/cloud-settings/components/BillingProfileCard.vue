<script setup lang="ts">
import { useTranslation } from '../translation'
import { getBillingProfile, saveBillingProfile } from '@frappe/cloud-sdk/api'
import { Button, ErrorMessage, Select, Skeleton, TextInput } from 'frappe-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useErrorMessage, type Store } from '../store'

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
  address_line1: '',
  city: '',
  state: '',
  country: '',
  pincode: '',
  gstin: '',
})

type Field = keyof typeof form

// Central needs these to invoice and to pick the payment gateway. India also needs the state for GST.
const isIndia = computed(() => form.country.trim().toLowerCase() === 'india')
const required = computed<Field[]>(() => [
  'legal_name',
  'address_line1',
  'city',
  'country',
  'currency',
  ...(isIndia.value ? (['state'] as Field[]) : []),
])

const FIELDS = computed(() => [
  { key: 'legal_name' as Field, label: __('Legal name'), autocomplete: 'organization', placeholder: __('Enter legal name') },
  { key: 'email' as Field, label: __('Billing email'), type: 'email', autocomplete: 'email', placeholder: __('Enter billing email') },
  { key: 'address_line1' as Field, label: __('Address'), autocomplete: 'address-line1', placeholder: __('Enter street address'), full: true },
  { key: 'city' as Field, label: __('City'), autocomplete: 'address-level2', placeholder: __('Enter city') },
  { key: 'state' as Field, label: __('State'), autocomplete: 'address-level1', placeholder: __('Enter state') },
  { key: 'country' as Field, label: __('Country'), autocomplete: 'country-name', placeholder: __('Enter country') },
  { key: 'pincode' as Field, label: __('PIN or ZIP code'), autocomplete: 'postal-code', placeholder: __('Enter PIN or ZIP code') },
  ...(isIndia.value ? [{ key: 'gstin' as Field, label: __('GSTIN'), placeholder: __('Enter GSTIN') }] : []),
])

const currencyIcons: Record<string, string> = {
  INR: 'lucide-indian-rupee',
  USD: 'lucide-dollar-sign',
  EUR: 'lucide-euro',
  GBP: 'lucide-pound-sterling',
  JPY: 'lucide-japanese-yen',
  CHF: 'lucide-swiss-franc',
  RUB: 'lucide-russian-ruble',
}

const currencies = ref<{ label: string; value: string; icon: string }[]>([])
const loaded = ref(false)
const working = ref(false)
const error = ref('')
const missing = ref<Field[]>([])

const noCurrencies = computed(() => loaded.value && !currencies.value.length)

// The gateway follows the currency, so suggest the one that fits the country until the customer picks one.
const currencyTouched = ref(false)
watch(() => form.country, (country) => {
  if (currencyTouched.value || !country.trim()) return
  const suggested = isIndia.value ? 'INR' : 'USD'
  if (currencies.value.some((option) => option.value === suggested)) form.currency = suggested
})

const MISSING: Partial<Record<Field, string>> = {
  legal_name: __('Enter the name to show on invoices.'),
  address_line1: __('Enter your street address.'),
  city: __('Enter your city.'),
  state: __('Enter your state. GST needs it.'),
  country: __('Enter your country.'),
  currency: __('Choose the currency you pay in.'),
}

const fieldError = (key: Field) => (missing.value.includes(key) ? MISSING[key] || '' : '')

const load = async () => {
  error.value = ''

  try {
    const profile = await getBillingProfile()

    currencies.value = (profile.supported_currencies || []).map((c) => {
      const option = typeof c === 'string' ? { label: c, value: c } : c

      return { ...option, icon: currencyIcons[option.value] || 'lucide-banknote' }
    })

    for (const key of Object.keys(form) as Field[]) form[key] = profile[key] || ''
    currencyTouched.value = Boolean(form.currency)

    loaded.value = true
  } catch (exception) {
    error.value = getErrorMessage(exception)
  }
}

onMounted(load)

const save = async () => {
  if (working.value) return

  missing.value = required.value.filter((key) => !String(form[key] || '').trim())
  if (missing.value.length) return

  working.value = true
  error.value = ''

  try {
    await saveBillingProfile({ ...form, gstin: isIndia.value ? form.gstin : '' })
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
      {{ __("Shown on your invoices. Your currency sets how you pay.") }}
    </p>

    <ErrorMessage :message="error" class="mt-4" />

    <Skeleton v-if="!loaded && !error" class="mt-4 h-20 rounded-6" />

    <div v-else-if="!loaded" class="mt-4 flex justify-end gap-2">
      <Button :label="__('Cancel')" @click="emit('close')" />
      <Button variant="solid" :label="__('Try again')" @click="load" />
    </div>

    <form v-else class="mt-4" novalidate @submit.prevent="save">
      <div class="grid gap-4 sm:grid-cols-2">
        <div v-for="field in FIELDS" :key="field.key" :class="field.full && 'sm:col-span-2'">
          <TextInput
            v-model="form[field.key]"
            :type="field.type || 'text'"
            :label="field.label"
            :required="required.includes(field.key)"
            :placeholder="field.placeholder"
            :autocomplete="field.autocomplete"
            :disabled="working"
            :aria-invalid="Boolean(fieldError(field.key)) || undefined"
          />
          <ErrorMessage :message="fieldError(field.key)" class="mt-1" />
        </div>

        <div>
          <Select
            v-model="form.currency"
            :label="__('Currency')"
            required
            :placeholder="__('Choose a currency')"
            :options="currencies"
            :disabled="working"
            @update:model-value="currencyTouched = true"
          />
          <ErrorMessage :message="fieldError('currency')" class="mt-1" />
        </div>
      </div>

      <p v-if="noCurrencies" class="mt-4 text-p-sm text-ink-amber-8">
        {{ __("No billing currencies are set up yet. Contact support to add a payment method.") }}
      </p>

      <div class="mt-5 flex justify-end gap-2">
        <Button :disabled="working" :label="__('Cancel')" @click="emit('close')" />
        <Button type="submit" variant="solid" :loading="working" :label="__('Continue')" />
      </div>
    </form>
  </section>
</template>
