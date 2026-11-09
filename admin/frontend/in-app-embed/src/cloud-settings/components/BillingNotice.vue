<script setup lang="ts">
import type { BillingSummary, Invoice } from '@frappe/cloud-sdk'
import { Alert, type AlertAction } from 'frappe-ui'
import { computed } from 'vue'
import { openExternal } from '../external'
import { formatDueDate, formatInvoiceMonth, type InvoicePayment } from '../invoicePayment'
import { useTranslation } from '../translation'

const __ = useTranslation()

interface Props {
  billing: BillingSummary
  invoices: Invoice[] | null
  payment: InvoicePayment
  billingUrl?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{ 'change-card': [] }>()

type Notice = {
  title: string
  description: string
  theme: 'red' | 'amber' | 'blue'
  primary?: AlertAction
  secondary?: AlertAction
}

const getCheckoutNotice = (): Notice | null => {
  const checkout = props.payment.checkout
  if (!checkout) return null

  return {
    title: __('Finish paying in the checkout tab'),
    description: __('Come back here after you pay your {0} invoice.', [formatInvoiceMonth(checkout.invoice.period_start)]),
    theme: 'blue',
    primary: { label: __('Check payment'), loading: props.payment.isChecking, onClick: props.payment.check },
    secondary: { label: __('Reopen checkout'), onClick: props.payment.reopen },
  }
}

const getUnpaidInvoiceNotice = (): Notice | null => {
  const invoice = props.invoices?.find((each) => each.status === 'Overdue' || each.status === 'Outstanding')
  if (!invoice) return null

  const month = formatInvoiceMonth(invoice.period_start)
  const theme = invoice.status === 'Overdue' ? 'red' : 'amber'
  if (!invoice.can_pay)
    return {
      title: __('Your team has an unpaid invoice for {0}', [month]),
      description: __('It also covers your other sites, so pay it from your Frappe Cloud billing page.'),
      theme,
      primary: props.billingUrl ? { label: __('Open billing page'), onClick: () => openExternal(props.billingUrl) } : undefined,
    }

  return {
    title: theme === 'red' ? __('Your {0} invoice is overdue', [month]) : __('Your {0} invoice is unpaid', [month]),
    description: theme === 'red' ? __('Pay it now to keep your site running.') : __('Pay it now so it does not become overdue.'),
    theme,
    primary: { label: __('Pay {0}', [invoice.amount]), loading: props.payment.opening === invoice.name, onClick: () => props.payment.pay(invoice) },
  }
}

// Central sends why the next charge would fail; the words are the app's.
const getBlockerNotice = (): Notice | null => {
  const bill = props.billing.next_bill
  const blocker = bill?.blockers[0]
  if (!bill || !blocker) return null

  const dueOn = formatDueDate(bill.charge_on)
  const addCard = { label: __('Add card'), onClick: () => emit('change-card') }
  const updateCard = { label: __('Update card'), onClick: () => emit('change-card') }
  const needsCard: Notice = {
    title: __('Add a card before {0}', [dueOn]),
    description: __('Your next payment of {0} is charged to it automatically.', [bill.amount]),
    theme: 'amber',
    primary: addCard,
  }
  const notices: Record<string, Notice> = {
    no_settlement_source: needsCard,
    no_active_method: needsCard,
    credits_shortfall: { title: __("Your credit won't cover your next payment"), description: __('Add a card to pay the rest of {0} on {1}.', [bill.amount, dueOn]), theme: 'amber', primary: addCard },
    card_expiring: { title: __('Your card expires before {0}', [dueOn]), description: __('Update it so your payment of {0} goes through.', [bill.amount]), theme: 'amber', primary: updateCard },
    mandate_reauth_pending: { title: __('Approve automatic payments again'), description: __('Your bank asked you to confirm automatic payments for this card.'), theme: 'amber', primary: updateCard },
    over_mandate_cap: { title: __('You pay your next payment yourself'), description: __("{0} is above your card's automatic payment limit. We will email you a link to pay it on {1}.", [bill.amount, dueOn]), theme: 'blue' },
    over_silent_threshold: { title: __('Your next payment needs your approval'), description: __('We will email you a link to approve {0} on {1}.', [bill.amount, dueOn]), theme: 'blue' },
    on_session_mode: { title: __('You pay each bill yourself'), description: __('We will email you a link when your bill is ready.'), theme: 'blue' },
  }

  return notices[blocker.code] || { title: blocker.title, description: blocker.fix || '', theme: 'amber' }
}

// One alert at a time: an open checkout, then money already owed, then the next charge.
const notice = computed(() => getCheckoutNotice() || getUnpaidInvoiceNotice() || getBlockerNotice())
</script>

<template>
  <Alert
    v-if="notice"
    :theme="notice.theme"
    :title="notice.title"
    :description="notice.description"
    :primary-action="notice.primary"
    :secondary-action="notice.secondary"
  />
</template>
