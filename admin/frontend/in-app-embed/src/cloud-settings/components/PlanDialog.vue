<script setup lang="ts">
import { Badge, Button, Dialog, ErrorMessage, Select } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { openExternal } from '../external'
import type { Store } from '../store'

interface Props {
  store: Store
}

const props = defineProps<Props>()
const emit = defineEmits(['open-billing'])
const open = defineModel({ type: Boolean, default: false })

const options = ref(null)
const selected = ref('')
const selectedProvider = ref('')
const selectedRegion = ref('')
const loading = ref(false)
const submitting = ref(false)
const error = ref('')

const plans = computed(() => options.value?.plans || [])
const current = computed(() => options.value?.current || '')

const canChange = computed(
  () => Boolean(selected.value) && selected.value !== current.value && options.value?.sufficient,
)

const providerOptions = computed(() =>
  normalizeChoices(options.value?.providers, options.value?.provider),
)

const regionOptions = computed(() =>
  normalizeChoices(options.value?.regions, options.value?.region),
)

const showPlacement = computed(
  () => providerOptions.value.length > 0 || regionOptions.value.length > 0,
)

const compareUrl = computed(
  () => options.value?.compare_url || props.store.state.context?.account_url || '',
)

const isSelected = (plan) => {
  return selected.value === plan.name
}

const isCustom = (plan) => {
  return Boolean(plan.is_custom) || /custom/i.test(plan.title || plan.name || '')
}

const normalizeChoices = (list, fallback) => {
  if (Array.isArray(list) && list.length) {
    return list.map((item) => ({
      label: item.label || item.name || item.value,
      value: item.value || item.name || item.label,
      logo_url: item.logo_url || '',
      icon: item.icon || '',
      is_current: Boolean(item.is_current),
    }))
  }

  if (!fallback) return []

  return [
    {
      label: fallback,
      value: fallback,
      logo_url: '',
      icon: '',
      is_current: true,
    },
  ]
}

const choiceIsCurrent = (choices, value) => {
  const match = choices.find((item) => item.value === value)

  return Boolean(match?.is_current || (choices.length === 1 && match))
}

watch(open, (isOpen) => {
  if (isOpen) {
    selectedProvider.value = ''
    selectedRegion.value = ''

    load()
  }
})

const load = async () => {
  loading.value = true
  error.value = ''
  selected.value = ''

  try {
    options.value = await props.store.api.getPlanOptions({
      provider: selectedProvider.value || undefined,
      region: selectedRegion.value || undefined,
    })

    selected.value = options.value?.current || ''
    selectedProvider.value =
      options.value?.provider ||
      normalizeChoices(options.value?.providers, options.value?.provider)[0]?.value ||
      ''
    selectedRegion.value =
      options.value?.region ||
      normalizeChoices(options.value?.regions, options.value?.region)[0]?.value ||
      ''
  } catch (exception) {
    error.value = props.store.api.getErrorMessage(exception)
  } finally {
    loading.value = false
  }
}

const onPlacementChange = async () => {
  if (loading.value || submitting.value) return

  await load()
}

const submit = async () => {
  if (!canChange.value || submitting.value) return

  submitting.value = true
  error.value = ''

  try {
    await props.store.api.changePlan(selected.value)
    await props.store.loadBilling(true)

    frappe.show_alert({
      message: __('Plan change has been queued.'),
      indicator: 'green',
    })

    open.value = false
  } catch (exception) {
    error.value = props.store.api.getErrorMessage(exception)
  } finally {
    submitting.value = false
  }
}

const openBilling = () => {
  open.value = false

  emit('open-billing')
}

const comparePlans = () => {
  openExternal(compareUrl.value)
}
</script>

<template>
  <Dialog v-model="open" :title="__('Change plan')" size="xl" :dismissible="!submitting">
    <template #default>
      <div v-if="loading" class="py-10 text-center text-p-sm text-ink-gray-5">
        {{ __("Loading plans") }}
      </div>

      <template v-else>
        <div
          v-if="showPlacement"
          class="mb-4 grid gap-3"
          :class="
            providerOptions.length && regionOptions.length
              ? 'sm:grid-cols-2'
              : 'grid-cols-1'
          "
        >
          <Select
            v-if="providerOptions.length"
            v-model="selectedProvider"
            :label="__('Provider')"
            :options="providerOptions"
            variant="outline"
            :disabled="submitting"
            @update:model-value="onPlacementChange"
          >
            <template #item-prefix="{ item }">
              <img
                v-if="item.logo_url"
                class="size-4 shrink-0 object-contain"
                :src="item.logo_url"
                alt=""
              />

              <span
                v-else-if="item.icon?.startsWith('lucide-')"
                :class="[item.icon, 'size-4 shrink-0 text-ink-gray-6']"
                aria-hidden="true"
              />

              <span
                v-else-if="item.icon"
                class="inline-flex size-4 shrink-0 items-center justify-center text-base leading-none"
                aria-hidden="true"
              >
                {{ item.icon }}
              </span>
            </template>

            <template #suffix>
              <Badge
                v-if="choiceIsCurrent(providerOptions, selectedProvider)"
                theme="gray"
                size="sm"
                :label="__('Current')"
              />

              <span
                class="lucide-chevron-down ml-auto size-4 shrink-0 text-ink-gray-4"
                aria-hidden="true"
              />
            </template>
          </Select>

          <Select
            v-if="regionOptions.length"
            v-model="selectedRegion"
            :label="__('Region')"
            :options="regionOptions"
            variant="outline"
            :disabled="submitting"
            @update:model-value="onPlacementChange"
          >
            <template #item-prefix="{ item }">
              <img
                v-if="item.logo_url"
                class="size-4 shrink-0 object-contain"
                :src="item.logo_url"
                alt=""
              />

              <span
                v-else-if="item.icon?.startsWith('lucide-')"
                :class="[item.icon, 'size-4 shrink-0 text-ink-gray-6']"
                aria-hidden="true"
              />

              <span
                v-else-if="item.icon"
                class="inline-flex size-4 shrink-0 items-center justify-center text-base leading-none"
                aria-hidden="true"
              >
                {{ item.icon }}
              </span>
            </template>

            <template #suffix>
              <Badge
                v-if="choiceIsCurrent(regionOptions, selectedRegion)"
                theme="gray"
                size="sm"
                :label="__('Current')"
              />

              <span
                class="lucide-chevron-down ml-auto size-4 shrink-0 text-ink-gray-4"
                aria-hidden="true"
              />
            </template>
          </Select>
        </div>

        <div
          v-if="plans.length"
          class="space-y-2"
          role="radiogroup"
          :aria-label="__('Available plans')"
        >
          <button
            v-for="plan in plans"
            :key="plan.name"
            type="button"
            role="radio"
            :aria-checked="isSelected(plan)"
            class="flex min-h-12 w-full items-center gap-2 rounded-6 border border-outline-gray-2 px-3.5 py-3 text-left hover:bg-surface-gray-2"
            :disabled="submitting"
            @click="selected = plan.name"
          >
            <span
              class="mr-1 shrink-0 text-ink-gray-5"
              :class="
                isSelected(plan)
                  ? 'lucide-circle-dot text-ink-gray-9'
                  : 'lucide-circle'
              "
              aria-hidden="true"
            />

            <span class="shrink-0 text-base-medium text-ink-gray-8">
              {{ plan.title }}
            </span>

            <Badge v-if="plan.is_current" theme="gray" size="sm" :label="__('Current')" />

            <span
              v-if="isCustom(plan)"
              class="lucide-sliders-horizontal size-3.5 shrink-0 text-ink-gray-5"
              aria-hidden="true"
            />

            <span class="min-w-0 flex-1 truncate text-p-sm text-ink-gray-5">
              {{ plan.subtitle }}
            </span>

            <span class="ml-1 shrink-0 text-base-medium text-ink-gray-8">
              {{ plan.price }}
            </span>
          </button>
        </div>

        <p v-else class="py-8 text-center text-p-sm text-ink-gray-5">
          {{ __("No plans are available for this server right now.") }}
        </p>

        <div
          v-if="plans.length && !options?.sufficient"
          class="mt-4 flex items-center gap-2 rounded-6 bg-surface-red-1 px-3 py-2.5 text-p-sm text-ink-red-8"
          role="alert"
        >
          <span class="lucide-info size-4 shrink-0 text-ink-red-8" aria-hidden="true" />
          {{ __("Insufficient balance — set up billing to switch.") }}

          <button
            type="button"
            class="ml-auto shrink-0 font-medium underline underline-offset-2"
            @click="openBilling"
          >
            {{ __("Go to Billing") }}
          </button>
        </div>

        <ErrorMessage :message="error" class="mt-4" />
      </template>
    </template>

    <template #actions>
      <div class="flex items-center gap-2">
        <Button class="mr-auto" :disabled="submitting || !compareUrl" @click="comparePlans">
          {{ __("Compare plans") }}
        </Button>
        <Button :disabled="submitting" @click="open = false" :label="__('Cancel')" />

        <Button variant="solid" :loading="submitting" :disabled="!canChange" @click="submit">
          {{ __("Change plan") }}
        </Button>
      </div>
    </template>
  </Dialog>
</template>
