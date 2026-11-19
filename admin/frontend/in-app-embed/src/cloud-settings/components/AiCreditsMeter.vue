<script setup lang="ts">
import type { AiCredits } from '@frappe/cloud-sdk'
import { computed } from 'vue'
import { useTranslation } from '../translation'

const __ = useTranslation()

interface Props {
  credits: AiCredits
}

const props = defineProps<Props>()

const isEnded = computed(() => props.credits.state === 'Ended')
const isMonthly = computed(() => props.credits.window === 'Monthly')

const usedPercent = computed(() => {
  if (isEnded.value || !props.credits.cap) return 100
  return Math.min(100, Math.round(((props.credits.spent || 0) / props.credits.cap) * 100))
})

const leftPercent = computed(() => 100 - usedPercent.value)
const isUsedUp = computed(() => usedPercent.value >= 100)
const isLow = computed(() => !isUsedUp.value && usedPercent.value >= 80)

const tone = computed(() => {
  if (isUsedUp.value) return { ink: 'text-ink-red-6', bar: 'bg-surface-red-5' }
  if (isLow.value) return { ink: 'text-ink-amber-6', bar: 'bg-surface-amber-3' }
  return { ink: 'text-ink-gray-8', bar: 'bg-surface-gray-7' }
})

const value = computed(() => {
  if (isEnded.value) return __('Ended')
  if (isUsedUp.value) return __('Used up')
  return __('{0}% left', [leftPercent.value])
})

// Grove counts a monthly cap over the UTC month.
const resetsOn = computed(() => {
  const now = new Date()
  const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1))
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(first)
})

// The product says what its AI features are. The state message wins once the credit runs low.
const purpose = computed(() => props.credits.note || __('AI features in your apps use this credit.'))

const trialDetail = computed(() => {
  if (isEnded.value) return __('The AI credit of your free trial has ended.')
  if (isUsedUp.value) return __('AI features in your apps are paused. Upgrade to turn them back on.')
  if (isLow.value) return __('AI features pause when it runs out. Upgrade to keep using them.')
  if (!props.credits.spent) return __('Not used yet. {0}', [purpose.value])
  return purpose.value
})

const monthlyDetail = computed(() => {
  if (isUsedUp.value) return __("This month's AI limit is reached. It resets on {0}.", [resetsOn.value])
  if (isLow.value) return __("Most of this month's AI limit is used. It resets on {0}.", [resetsOn.value])
  return __('{0} It resets on {1}.', [purpose.value, resetsOn.value])
})
</script>

<template>
  <div class="py-4">
    <div class="flex items-baseline justify-between gap-4">
      <p class="text-sm text-ink-gray-5">{{ isMonthly ? __('AI usage') : __('AI credit') }}</p>
      <p :class="['text-base-semibold tabular-nums', tone.ink]">{{ value }}</p>
    </div>
    <div
      class="my-2 h-1.5 overflow-hidden rounded-full bg-surface-gray-2"
      role="progressbar"
      :aria-label="isMonthly ? __('AI limit left this month') : __('AI credit left')"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="leftPercent"
    >
      <div :class="['h-full rounded-full', tone.bar]" :style="{ width: `${leftPercent}%` }" />
    </div>
    <div class="flex items-center justify-between gap-4">
      <p class="text-p-sm text-ink-gray-5">{{ isMonthly ? monthlyDetail : trialDetail }}</p>
      <div v-if="$slots.default && !isMonthly && (isLow || isUsedUp)" class="shrink-0"><slot /></div>
    </div>
  </div>
</template>
