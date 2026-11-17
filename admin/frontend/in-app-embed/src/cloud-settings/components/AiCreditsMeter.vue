<script setup lang="ts">
import type { AiCredits } from '@frappe/cloud-sdk'
import { Progress } from 'frappe-ui'
import { computed } from 'vue'
import { useTranslation } from '../translation'

const __ = useTranslation()

interface Props {
  credits: AiCredits
}

const props = defineProps<Props>()

const isEnded = computed(() => props.credits.state === 'Ended')

const percent = computed(() => {
  if (isEnded.value || !props.credits.cap) return 100
  return Math.min(100, Math.round(((props.credits.spent || 0) / props.credits.cap) * 100))
})

const ink = computed(() => {
  if (isEnded.value || percent.value >= 100) return 'text-ink-red-6'
  if (percent.value >= 80) return 'text-ink-amber-6'
  return 'text-ink-gray-6'
})

// Grove counts a monthly cap over the UTC month.
const resetsOn = computed(() => {
  const now = new Date()
  const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1))
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(first)
})

const label = computed(() => {
  if (isEnded.value) return __('AI credit ended')
  if (props.credits.window === 'Monthly')
    return __("{0}% of this month's AI limit used, resets on {1}", [percent.value, resetsOn.value])
  if (percent.value >= 100) return __('AI credit included, all used')
  return __('AI credit included, {0}% used', [percent.value])
})
</script>

<template>
  <div class="flex items-center gap-2">
    <div class="w-16 shrink-0"><Progress size="md" :value="percent" /></div>
    <span :class="['text-sm tabular-nums', ink]">{{ label }}</span>
  </div>
</template>
