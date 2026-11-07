<script setup lang="ts">
import { Combobox, TextInput } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useTranslation } from '../translation'
import { COUNTRY_DIAL_CODES } from './countryDialCodes'

const __ = useTranslation()

interface Props {
  label: string
  /** The address country. It picks the calling code until the user picks one. */
  country?: string
  required?: boolean
  disabled?: boolean
  error?: string
}

const props = withDefaults(defineProps<Props>(), { country: '', error: '' })
const phone = defineModel<string>({ default: '' })

const getDialCode = (country: string) => COUNTRY_DIAL_CODES.find(([name]) => name === country)?.[2] || ''

// A stored number has no country, so prefer the address country when several share its code, such as +1.
const getCountryOf = (compact: string) => {
  const matches = COUNTRY_DIAL_CODES.filter(([, , dialCode]) => compact.startsWith(dialCode))
  const longest = Math.max(0, ...matches.map(([, , dialCode]) => dialCode.length))
  const best = matches.filter(([, , dialCode]) => dialCode.length === longest)

  return best.find(([name]) => name === props.country)?.[0] || best[0]?.[0] || ''
}

const compact = phone.value.replace(/[\s()-]/g, '')
const country = ref(compact.startsWith('+') ? getCountryOf(compact) : props.country)
const number = ref(country.value ? compact.slice(getDialCode(country.value).length) : phone.value)
const isCountryPicked = ref(Boolean(compact))

// Search by name, ISO code or calling code, as Press's phone field does. The code is the label, so the field shows "+1".
// The chosen country stays first in the results, so the field keeps showing its code while a search runs.
const query = ref('')
const options = computed(() => {
  const term = query.value.trim().toLowerCase().replace(/^\+/, '')
  const matches = COUNTRY_DIAL_CODES.filter(
    ([name, isoCode, dialCode]) =>
      name === country.value || !term || name.toLowerCase().includes(term) || isoCode.toLowerCase() === term || dialCode.slice(1).startsWith(term),
  )

  const chosen = matches.filter(([name]) => name === country.value)
  return [...chosen, ...matches.filter(([name]) => name !== country.value)]
    .map(([name, , dialCode]) => ({ label: dialCode, value: name, description: name }))
})

watch(() => props.country, (value) => {
  if (!isCountryPicked.value && value) country.value = value
})

const dialCode = computed(() => getDialCode(country.value))

// Frappe's Phone fields store "+91-9876543210", and Central strips the separators.
watch([dialCode, number], () => {
  const digits = number.value.trim()
  phone.value = digits && dialCode.value ? `${dialCode.value}-${digits}` : digits
})

// The combobox writes the picked option into the search, so each opening starts a fresh search.
const clearSearchOnOpen = (isOpen: boolean) => {
  if (isOpen) query.value = ''
}

const pickCountry = (value?: string | number | null) => {
  country.value = String(value || '')
  isCountryPicked.value = true
}
</script>

<template>
  <div class="grid grid-cols-[6.5rem_minmax(0,1fr)] items-start gap-2">
    <Combobox
      v-model:query="query"
      trigger="button"
      :label="__('Code')"
      :model-value="country || null"
      :options="options"
      :filterable="false"
      :placeholder="__('+ Code')"
      :empty-text="__('No country matches.')"
      :disabled="disabled"
      @update:model-value="pickCountry"
      @update:open="clearSearchOnOpen"
    />

    <TextInput
      v-model="number"
      type="tel"
      name="phone"
      autocomplete="tel-national"
      :label="label"
      :required="required"
      :error="error"
      :placeholder="__('Enter phone number')"
      :disabled="disabled"
    />
  </div>
</template>
