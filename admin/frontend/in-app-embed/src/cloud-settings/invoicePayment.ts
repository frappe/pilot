import { createInvoiceCheckout, getCheckoutStatus } from '@frappe/cloud-sdk/api'
import type { Invoice } from '@frappe/cloud-sdk'
import { toast } from 'frappe-ui'
import { reactive } from 'vue'
import { openExternal } from './external'
import { useErrorMessage } from './store'
import { useTranslation } from './translation'

export const formatInvoiceMonth = (date: string) =>
  new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(new Date(date))

export const formatDueDate = (date?: string | null) =>
  date ? new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' }).format(new Date(date)) : ''

/** Paying one invoice in the gateway's tab, then confirming it here. One per billing panel, so
 * the alert and the invoice table share the open checkout. */
export const useInvoicePayment = (onPaid: () => Promise<void>) => {
  const __ = useTranslation()
  const getErrorMessage = useErrorMessage()

  const payment = reactive({
    opening: '',
    isChecking: false,
    checkout: null as { invoice: Invoice; reference: string; url: string } | null,

    async pay(invoice: Invoice) {
      payment.opening = invoice.name
      try {
        const session = await createInvoiceCheckout(invoice.name, window.location.href)
        payment.checkout = { invoice, reference: session.reference, url: session.checkout_url }
        openExternal(session.checkout_url)
      } catch (exception) {
        toast.error(getErrorMessage(exception))
      } finally {
        payment.opening = ''
      }
    },

    reopen() {
      openExternal(payment.checkout?.url)
    },

    async check() {
      if (!payment.checkout) return

      payment.isChecking = true
      try {
        const { status } = await getCheckoutStatus(payment.checkout.reference)
        if (status !== 'paid') {
          toast.info(__("We haven't received the payment yet. Finish it in the checkout tab."))
          return
        }

        payment.checkout = null
        toast.success(__('Invoice paid.'))
        await onPaid()
      } catch (exception) {
        toast.error(getErrorMessage(exception))
      } finally {
        payment.isChecking = false
      }
    },
  })

  return payment
}

export type InvoicePayment = ReturnType<typeof useInvoicePayment>
