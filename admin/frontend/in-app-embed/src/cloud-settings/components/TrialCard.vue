<script setup lang="ts">
import { useTranslation } from '../translation'
import { Alert } from 'frappe-ui'
import type { BillingTrial } from '@frappe/cloud-sdk'
import { computed } from 'vue'

const __ = useTranslation()

interface Props {
  trial: BillingTrial
  /** The plan the site runs on, named in the billing note. */
  planName?: string
  /** Hides the action while the payment steps are open, so the panel keeps one primary button. */
  isAdding?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{ 'add-payment': [] }>()

const formatDate = (value?: string | null) =>
  value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value)) : ''

const isEnded = computed(() => props.trial.state === 'Ended')
const plan = computed(() => props.planName || __('paid'))

const theme = computed(() => {
  if (isEnded.value) return 'red'
  if (props.trial.held) return 'amber'
  return 'blue'
})

const title = computed(() => {
  if (isEnded.value) return __('Your free trial has ended')
  if (props.trial.held) return __('Your site is still on the free trial')
  if (props.trial.days_left <= 0) return __('Your free trial ends today')
  if (props.trial.days_left === 1) return __('Your free trial ends tomorrow')
  return __('Your free trial ends in {0} days', [props.trial.days_left])
})

const description = computed(() => {
  if (isEnded.value)
    return __('This site is stopped. To keep the site and its data, add a payment method before {0}. Billing for the {1} plan then starts.', [formatDate(props.trial.deletes_on), plan.value])
  if (props.trial.held)
    return __('Your payment method is added, but the {0} plan is above your spending limit. Contact support to raise the limit.', [plan.value])

  return __('Your site is free until {0}. To keep the site, add a payment method. Billing for the {1} plan then starts, and full server access turns on.', [formatDate(props.trial.ends_on), plan.value])
})

const action = computed(() =>
  props.trial.held || props.isAdding
    ? undefined
    : { label: __('Add payment method'), variant: 'solid' as const, onClick: () => emit('add-payment') },
)
</script>

<template>
  <Alert :theme="theme" :title="title" :description="description" :primary-action="action" />
</template>
