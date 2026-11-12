<script setup lang="ts">
import type { BillingSummary } from '@frappe/cloud-sdk'
import { Button } from 'frappe-ui'
import { List, ListCell, ListRow } from 'frappe-ui/list'
import { computed, ref } from 'vue'
import { formatDueDate } from '../invoicePayment'
import { useTranslation } from '../translation'
import BillBreakdown from './BillBreakdown.vue'
import BillingRow from './BillingRow.vue'

const __ = useTranslation()

interface Props {
  billing: BillingSummary
}

const props = defineProps<Props>()
const emit = defineEmits<{ 'change-plan': []; 'change-card': []; 'edit-details': [] }>()

const isBreakdownOpen = ref(false)
const bill = computed(() => props.billing.next_bill)
const card = computed(() => props.billing.payment_method)
const plan = computed(() => props.billing.plan)

const billDetail = computed(() => {
  if (!bill.value) return ''
  const dueOn = formatDueDate(bill.value.charge_on)
  const due = dueOn
    ? __('Estimate, due {0} in {1} days', [dueOn, bill.value.days_left])
    : __('Estimate, due in {0} days', [bill.value.days_left])
  return bill.value.tax ? __('{0} · includes {1} {2}', [due, bill.value.tax, bill.value.tax_type]) : due
})

const creditDetail = computed(() =>
  card.value && props.billing.credit?.available ? __('Your {0} credit is used first.', [props.billing.credit.amount]) : '',
)
</script>

<template>
  <section class="rounded-6 border border-outline-gray-1.5 px-4" :aria-label="__('Plan and payment')">
    <List :columns="['minmax(0,1fr)', 'auto']">
      <BillingRow v-if="bill" :label="__('Next payment')">
        <template #value>
          <span class="text-xl-semibold tabular-nums text-ink-gray-9">{{ bill.amount }}</span>
        </template>
        <template #detail>
          <span class="text-p-sm text-ink-gray-5">
            {{ billDetail }}
            <template v-if="bill.lines.length">
              ·
              <!-- Central's billing page links its breakdown the same way. -->
              <button
                type="button"
                class="text-ink-gray-8 underline decoration-dotted underline-offset-[3px] transition-colors hover:decoration-solid"
                :aria-expanded="isBreakdownOpen"
                aria-controls="next-payment-breakdown"
                @click="isBreakdownOpen = !isBreakdownOpen"
              >
                {{ isBreakdownOpen ? __("Hide breakdown") : __("See breakdown") }}
              </button>
            </template>
          </span>
        </template>
      </BillingRow>

      <ListRow v-if="bill && isBreakdownOpen" id="next-payment-breakdown" class="pb-3">
        <ListCell class="col-span-full"><BillBreakdown :bill="bill" /></ListCell>
      </ListRow>

      <BillingRow
        :label="__('Plan')"
        :value="plan?.name"
        :detail="plan?.subtitle !== plan?.name ? plan?.subtitle : ''"
      >
        <Button :label="__('Change plan')" @click="emit('change-plan')" />
      </BillingRow>

      <BillingRow
        :label="__('Pay with')"
        :value="card ? card.label : __('No card added')"
        :detail="creditDetail"
        :is-empty="!card"
      >
        <Button :label="card ? __('Change card') : __('Add card')" @click="emit('change-card')" />
      </BillingRow>

      <BillingRow :label="__('Billing details')" :value="__('Name, address and tax ID on your invoices')">
        <Button :label="__('Edit')" @click="emit('edit-details')" />
      </BillingRow>
    </List>
  </section>
</template>
