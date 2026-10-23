<script setup lang="ts">
import { useTranslation } from '../translation'
import { changePlan, getPlanOptions } from '@frappe/cloud-sdk/api'
import type { PlanOptions } from '@frappe/cloud-sdk'
import { Badge, Button, ErrorMessage, Skeleton } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { openExternal } from '../external'
import { useErrorMessage, type Store } from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
}

const props = defineProps<Props>()
const emit = defineEmits<{ close: [] }>()
const store = props.store

const options = ref<PlanOptions | null>(null)
const selected = ref('')
const loadError = ref('')
const submitError = ref('')
const submitting = ref(false)
const started = ref(false)

const billing = computed(() => store.state.billing)
const needsPaymentMethod = computed(() => Boolean(billing.value?.trial) && !billing.value?.payment_method)
const morePlansUrl = computed(() => store.state.accountLinks?.change_plan)
const choice = computed(() => options.value?.plans.find((plan) => plan.name === selected.value))
const canSubmit = computed(
  () => Boolean(choice.value) && !choice.value?.is_current && Boolean(options.value?.sufficient),
)

const load = async () => {
  loadError.value = ''
  options.value = null

  try {
    options.value = await getPlanOptions()
    selected.value = options.value.current || ''
  } catch (exception) {
    loadError.value = getErrorMessage(exception)
  }
}

const submit = async () => {
  if (!canSubmit.value || submitting.value) return

  submitting.value = true
  submitError.value = ''

  try {
    const result = await changePlan(selected.value)
    // The site goes offline while it resizes; the progress page lives off this server.
    if (result.progress_url) return window.location.assign(result.progress_url)

    started.value = true
    await store.loadBilling(true)
  } catch (exception) {
    submitError.value = getErrorMessage(exception)
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  if (!needsPaymentMethod.value) load()
})
</script>

<template>
  <section class="rounded-6 border border-outline-gray-2 p-5">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h3 class="text-base-semibold text-ink-gray-8">{{ __("Change plan") }}</h3>
        <p v-if="!needsPaymentMethod" class="mt-1 text-p-sm text-ink-gray-5">
          {{ __("Pick the size this site runs on. You pay for the new plan from today.") }}
        </p>
      </div>
      <Button variant="ghost" icon="lucide-x" :aria-label="__('Close plan list')" @click="emit('close')" />
    </div>

    <p v-if="needsPaymentMethod" class="mt-4 rounded-6 bg-surface-gray-2 p-4 text-p-sm text-ink-gray-7">
      {{ __("Other plans open once you add a payment method.") }}
    </p>

    <div v-else-if="started" class="mt-4 rounded-6 bg-surface-gray-2 p-4" role="status">
      <p class="text-p-sm text-ink-gray-7">
        {{ __("Your plan change has started. Your site restarts in a few minutes.") }}
      </p>
    </div>

    <div v-else-if="loadError" class="mt-4 space-y-3">
      <ErrorMessage :message="loadError" />
      <Button :label="__('Try again')" @click="load" />
    </div>

    <div v-else-if="!options" class="mt-4 space-y-2" aria-busy="true" :aria-label="__('Loading plans')">
      <Skeleton v-for="index in 3" :key="index" class="h-14 rounded-6" />
    </div>

    <form v-else class="mt-4" @submit.prevent="submit">
      <fieldset class="space-y-2" :disabled="submitting">
        <legend class="sr-only">{{ __("Plans") }}</legend>

        <p v-if="!options.plans.length" class="text-p-sm text-ink-gray-5">
          {{ __("No other plans are available for this site here.") }}
        </p>

        <label
          v-for="plan in options.plans"
          :key="plan.name"
          class="flex cursor-pointer items-center gap-3 rounded-6 border px-4 py-3 transition-colors"
          :class="selected === plan.name ? 'border-outline-gray-4 bg-surface-gray-1' : 'border-outline-gray-2 hover:bg-surface-gray-1'"
        >
          <input v-model="selected" class="shrink-0" type="radio" name="plan" :value="plan.name" />
          <span class="min-w-0 flex-1">
            <span class="flex items-center gap-2 text-base-medium text-ink-gray-8">
              {{ plan.title }}
              <Badge v-if="plan.is_current" :label="__('Current')" theme="gray" />
            </span>
            <span class="mt-0.5 block text-p-sm text-ink-gray-5">{{ plan.subtitle }}</span>
          </span>
          <span class="shrink-0 text-base-medium tabular-nums text-ink-gray-8">{{ plan.price }}</span>
        </label>
      </fieldset>

      <p v-if="!options.sufficient" class="mt-3 text-p-sm text-ink-amber-6">
        {{ __("Your spending limit does not allow a bigger plan. Raise it in Frappe Cloud.") }}
      </p>

      <p v-else-if="canSubmit" class="mt-3 flex items-start gap-2 text-p-sm text-ink-gray-6">
        <span class="lucide-info mt-0.5 size-4 shrink-0" aria-hidden="true" />
        {{ __("Your site goes offline for a few minutes while it moves to {0}. A progress page brings you back when it is ready.", [choice?.title]) }}
      </p>

      <ErrorMessage :message="submitError" class="mt-3" />

      <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
        <Button
          variant="ghost"
          icon-right="lucide-arrow-up-right"
          :disabled="!morePlansUrl"
          :label="__('See more plans in Frappe Cloud')"
          @click="openExternal(morePlansUrl)"
        />
        <Button
          type="submit"
          variant="solid"
          :disabled="!canSubmit"
          :loading="submitting"
          :label="choice && !choice.is_current ? __('Move to {0}', [choice.title]) : __('Choose a plan')"
        />
      </div>
    </form>
  </section>
</template>
