<script setup lang="ts">
import { useTranslation } from '../translation'
import { changePlan, getPlanOptions } from '@frappe/cloud-sdk/api'
import type { PlanOption, PlanOptions } from '@frappe/cloud-sdk'
import { Alert, Badge, Button, ErrorMessage, Radio, RadioGroup, Skeleton } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { openExternal } from '../external'
import { useErrorMessage, type Store } from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
  /** Where this form sits in the upgrade steps, such as "Step 1 of 3". */
  step?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{ close: []; chosen: [plan: PlanOption] }>()
const store = props.store

const options = ref<PlanOptions | null>(null)
const selected = ref('')
const loadError = ref('')
const submitError = ref('')
const submitting = ref(false)
const started = ref(false)

const isTrial = computed(() => Boolean(store.state.billing?.trial))
const morePlansUrl = computed(() => store.state.accountLinks?.change_plan)
const choice = computed(() => options.value?.plans.find((plan) => plan.name === selected.value))
const canSubmit = computed(() => {
  if (!choice.value || !options.value?.sufficient) return false
  return isTrial.value || !choice.value.is_current
})

const load = async () => {
  loadError.value = ''
  options.value = null

  try {
    options.value = await getPlanOptions()
    selected.value = options.value.selected || options.value.current || options.value.plans[0]?.name || ''
  } catch (exception) {
    loadError.value = getErrorMessage(exception)
  }
}

const submit = async () => {
  if (!canSubmit.value || submitting.value || !choice.value) return

  submitting.value = true
  submitError.value = ''

  try {
    const result = await changePlan(selected.value)
    if (isTrial.value) return emit('chosen', choice.value)

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

const submitLabel = computed(() => {
  if (isTrial.value) return __('Continue')
  return choice.value && !choice.value.is_current ? __('Move to {0}', [choice.value.title]) : __('Choose a plan')
})

onMounted(load)
</script>

<template>
  <section>
    <p v-if="step" class="text-p-sm text-ink-gray-5">{{ step }}</p>
    <h3 class="text-base-semibold text-ink-gray-8" :class="step && 'mt-1'">
      {{ isTrial ? __("Choose your plan") : __("Change plan") }}
    </h3>
    <p class="mt-1 text-p-sm text-ink-gray-5">
      {{ isTrial
          ? __("Every plan gives you full server access: SSH, the web console, Pilot and snapshots. Billing starts when you add a payment method.")
          : __("Pick the size this site runs on. You pay for the new plan from today.") }}
    </p>

    <div v-if="started" class="mt-5 rounded-6 bg-surface-gray-2 p-4" role="status">
      <p class="text-p-sm text-ink-gray-7">
        {{ __("Your plan change has started. Your site restarts in a few minutes.") }}
      </p>
    </div>

    <div v-else-if="loadError" class="mt-5 space-y-3">
      <ErrorMessage :message="loadError" />
      <div class="flex gap-2">
        <Button :label="__('Cancel')" @click="emit('close')" />
        <Button :label="__('Try again')" @click="load" />
      </div>
    </div>

    <div v-else-if="!options" class="mt-5 space-y-3" aria-busy="true" :aria-label="__('Loading plans')">
      <Skeleton v-for="index in 3" :key="index" class="h-14 rounded-6" />
      <Button :label="__('Cancel')" @click="emit('close')" />
    </div>

    <form v-else class="flex flex-col gap-5 mt-5" @submit.prevent="submit">
      <p v-if="!options.plans.length" class="text-p-sm text-ink-gray-5">
        {{ isTrial
            ? __("No plan fits your spending limit. Contact support to raise it.")
            : __("No other plans are available for this site here.") }}
      </p>

      <RadioGroup
        v-else
        v-model="selected"
        class="plan-grid"
        :aria-label="__('Plans')"
        :disabled="submitting"
      >
        <div
          v-for="plan in options.plans"
          :key="plan.name"
          class="flex items-center gap-3 px-4 py-3 border rounded-6 transition-colors"
          :class="selected === plan.name ? 'border-outline-gray-5' : 'border-outline-gray-2'"
        >
          <Radio :value="plan.name" class="flex-1 min-w-0" :description="plan.subtitle">
            <template #label>
              <span class="flex items-center gap-2">
                {{ plan.title }}
                <Badge v-if="plan.is_current" :label="__('Current')" theme="gray" />
              </span>
            </template>
          </Radio>

          <span class="shrink-0 text-base-medium tabular-nums text-ink-gray-8">{{ plan.price }}</span>
        </div>
      </RadioGroup>

      <Alert
        v-if="options.plans.length && !isTrial"
        theme="amber"
        :title="__('Larger plans permanently increase your disk')"
        :description="__('You can switch back later, but the disk stays at the larger size.')"
      />

      <p v-if="!options.sufficient && !isTrial" class="text-p-sm text-ink-amber-6">
        {{ __("Your spending limit does not allow a bigger plan. Raise it in Frappe Cloud.") }}
      </p>

      <p v-else-if="canSubmit" class="flex items-start gap-2 text-p-sm text-ink-gray-6">
        <span class="lucide-info mt-0.5 size-4 shrink-0" aria-hidden="true" />
        {{ isTrial
            ? __("Your site restarts once, for a few minutes, to move to {0}. Its data stays as it is.", [choice?.title])
            : __("Your site goes offline for a few minutes while it moves to {0}. A progress page brings you back when it is ready.", [choice?.title]) }}
      </p>

      <ErrorMessage :message="submitError" />

      <div class="flex flex-wrap items-center gap-2">
        <Button
          v-if="!isTrial"
          variant="ghost"
          class="-ml-2 mr-auto"
          icon-right="lucide-arrow-up-right"
          :disabled="!morePlansUrl"
          :label="__('Check more plans')"
          @click="openExternal(morePlansUrl)"
        />

        <Button
          :class="isTrial && 'ml-auto'"
          :disabled="submitting"
          :label="isTrial ? __('Back') : __('Cancel')"
          @click="emit('close')"
        />

        <Button
          type="submit"
          variant="solid"
          :disabled="!canSubmit"
          :loading="submitting"
          :label="submitLabel"
        />
      </div>
    </form>

    <Button
      v-if="started"
      class="mt-5"
      :label="__('Back')"
      @click="emit('close')"
    />
  </section>
</template>

<style scoped>
.plan-grid :deep([role="radiogroup"]) {
  @apply grid gap-2;
}
</style>
