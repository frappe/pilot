<script setup lang="ts">
import { useTranslation } from '../translation'
import { Alert } from 'frappe-ui'
import type { BillingTrial } from '@frappe/cloud-sdk'
import { computed } from 'vue'

const __ = useTranslation()

interface Props {
  trial: BillingTrial
  /** Hides the action while the upgrade steps are open, so the panel keeps one primary button. */
  isUpgrading?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{ upgrade: [] }>()

const formatDate = (value?: string | null) =>
  value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value)) : ''

const isEnded = computed(() => props.trial.state === 'Ended')

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
    return __('Your site on Frappe Cloud is stopped. Upgrade before {0} to keep it and its data.', [formatDate(props.trial.deletes_on)])
  if (props.trial.held)
    return __('Your payment method is added, but the {0} plan is above your spending limit. Choose a smaller plan, or contact support to raise the limit.', [props.trial.plan?.title || __('chosen')])

  return __('This trial runs on Frappe Cloud until {0}. Upgrade to keep your site running.', [formatDate(props.trial.ends_on)])
})

const action = computed(() =>
  props.isUpgrading
    ? undefined
    : {
        label: props.trial.held ? __('Choose a plan') : __('Upgrade'),
        onClick: () => emit('upgrade'),
      },
)
</script>

<template>
  <Alert :theme="theme" :title="title" :description="description" :primary-action="action" />
</template>
