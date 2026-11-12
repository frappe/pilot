<script setup lang="ts">
import { useTranslation } from '../translation'
import {
  addPaymentMethod,
  confirmPaymentMethod,
  confirmPaymentMethodCheckout,
  createPaymentMethodCheckout,
  getBillingProfile,
  getPaymentGateways,
} from '@frappe/cloud-sdk/api'
import { Button, ErrorMessage } from 'frappe-ui'
import type { PaymentCheckout, PaymentGateway, PaymentMethodSetup } from '@frappe/cloud-sdk'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import UpiLogo from '../assets/UPI-1.svg?inline'
import { openExternal } from '../external'
import { holdRazorpayCheckout, releaseRazorpayCheckout } from '../razorpayCheckout'
import { useErrorMessage, type Store } from '../store'
import ChoiceTile from './ChoiceTile.vue'
import { GATEWAY_LOGOS } from './gatewayLogos'
import PanelSkeleton from './PanelSkeleton.vue'
import PaymentTrustNote from './PaymentTrustNote.vue'
import PhoneInput from './PhoneInput.vue'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
  /** Where this form sits in the payment steps, such as "Step 2 of 2". */
  step?: string
}

const props = defineProps<Props>()
const emit = defineEmits(['close', 'added'])
const store = props.store

const RAZORPAY_SDK = 'https://checkout.razorpay.com/v1/checkout.js'

const METHODS = [
  {
    value: 'Card',
    label: __('Card'),
    hint: __('Visa, Mastercard, RuPay, Amex'),
    icon: 'lucide-credit-card',
  },
  {
    value: 'UPI Autopay',
    label: __('UPI'),
    hint: __('Pay from any UPI app'),
    image: UpiLogo,
  },
]

const accountUrl = computed(() => store.state.context.account_url || '')

const gateways = ref<PaymentGateway[] | null>(null)
const method = ref('Card')
const selected = ref('')
const contact = ref('')
const profile = ref({ phone: '', country: '' })
const checkout = ref<PaymentCheckout | null>(null)
const message = ref('')
const working = ref(false)
const error = ref('')

const visibleGateways = computed(() =>
  (gateways.value || []).filter(
    (g) => method.value !== 'UPI Autopay' || g.adapter_key === 'Razorpay',
  ),
)

const gateway = computed(() => visibleGateways.value.find((g) => g.name === selected.value))

// UPI Autopay runs only through Razorpay, so it is offered only where a Razorpay gateway serves the currency.
const methods = computed(() =>
  METHODS.filter(
    (option) => option.value !== 'UPI Autopay' || (gateways.value || []).some((g) => g.adapter_key === 'Razorpay'),
  ),
)

const paymentNote = computed(() => {
  if (gateway.value?.adapter_key !== 'Razorpay')
    return __('We verify your card with a small refunded charge, then bill you at the end of each cycle.')
  if (method.value === 'UPI Autopay')
    return __('Razorpay asks you to approve payments up to your spending limit. We charge only your monthly bill, on its due date. We refund the ₹1 setup payment.')
  return __('Razorpay shows a high limit. We charge only your monthly bill and refund the ₹1 setup.')
})

// Razorpay needs a phone to save a card, which Central takes from the billing profile when it has one.
const needsContact = computed(
  () => method.value === 'Card' && gateway.value?.adapter_key === 'Razorpay' && !profile.value.phone,
)

const contactError = ref('')
const canContinue = computed(() => Boolean(gateway.value) && !working.value)

watch(
  visibleGateways,
  (list) => {
    if (!list.some((g) => g.name === selected.value)) selected.value = list[0]?.name || ''
  },
  { immediate: true },
)

const load = async () => {
  error.value = ''

  try {
    const [options, billingProfile] = await Promise.all([getPaymentGateways(), getBillingProfile()])
    gateways.value = options
    profile.value = { phone: billingProfile.phone || '', country: billingProfile.country || '' }
  } catch (exception) {
    error.value = getErrorMessage(exception)
  }
}

onMounted(load)
onBeforeUnmount(releaseRazorpayCheckout)

const start = () => {
  if (!canContinue.value || !gateway.value) return

  contactError.value = needsContact.value && !contact.value.trim() ? __('Enter a phone number. Razorpay needs it to save a card.') : ''
  if (contactError.value) return

  return gateway.value.adapter_key === 'Razorpay' ? startRazorpay() : startStripe()
}

const startStripe = async () => {
  await run(async () => {
    checkout.value = await createPaymentMethodCheckout(window.location.href, selected.value)

    message.value = __('Checkout opened in a new tab. Add your card there, then check its status.')

    openExternal(checkout.value.checkout_url)
  })
}

const startRazorpay = async () => {
  working.value = true
  error.value = ''
  message.value = ''

  try {
    const handles = await addPaymentMethod(
      method.value,
      selected.value,
      contact.value.trim() || null,
    )

    await loadRazorpay()
    openRazorpayCheckout(handles)
  } catch (exception) {
    error.value = getErrorMessage(exception)
    working.value = false
  }
}

interface RazorpayResponse {
  razorpay_payment_id: string
  razorpay_order_id: string
  razorpay_signature: string
}

interface RazorpayInstance {
  on: (event: string, handler: (response: { error?: { description?: string } }) => void) => void
  open: () => void
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance
  }
}

const openRazorpayCheckout = (handles: PaymentMethodSetup) => {
  if (!window.Razorpay) throw new Error(__('Could not load Razorpay Checkout.'))
  const rzp = new window.Razorpay({
    key: handles.key_id,
    order_id: handles.order_id,
    customer_id: handles.customer_id,
    recurring: handles.recurring ? 1 : undefined,
    name: __('Frappe Cloud'),
    description:
      method.value === 'UPI Autopay' ? __('Set up UPI Autopay') : __('Save card for billing'),
    prefill: handles.prefill || {},
    handler: (response: RazorpayResponse) => confirmRazorpay(handles.payment_method, response),
    modal: {
      ondismiss: () => {
        releaseRazorpayCheckout()
        working.value = false
        message.value = __('Setup cancelled.')
      },
    },
  })

  // The modal stays open after a decline so the customer can try another card.
  rzp.on('payment.failed', (response) => {
    error.value = response?.error?.description || __('Your bank did not approve this. Try again or use another card.')
    working.value = false
  })

  holdRazorpayCheckout()

  try {
    rzp.open()
  } catch (exception) {
    releaseRazorpayCheckout()
    throw exception
  }
}

const confirmRazorpay = async (paymentMethod: string, response: RazorpayResponse) => {
  releaseRazorpayCheckout()

  await run(async () => {
    const result = await confirmPaymentMethod({
      payment_method: paymentMethod,
      razorpay_payment_id: response.razorpay_payment_id,
      razorpay_order_id: response.razorpay_order_id,
      razorpay_signature: response.razorpay_signature,
    })

    if (result.status === 'Active') {
      await store.loadBilling(true)

      emit('added')
      return
    }

    error.value = __('Your payment method is saved but not active yet ({0}). Check again in a minute.', [result.status])
  })
}

const loadRazorpay = () => {
  return new Promise<void>((resolve, reject) => {
    if (window.Razorpay) return resolve()

    const script = document.createElement('script')

    script.src = RAZORPAY_SDK
    script.onload = () => resolve()
    script.onerror = () => reject(new Error(__('Could not load Razorpay Checkout.')))

    document.body.appendChild(script)
  })
}

const check = async () => {
  const reference = checkout.value?.reference
  if (!reference) return
  await run(async () => {
    const result = await confirmPaymentMethodCheckout(reference)

    if (result.active) {
      await store.loadBilling(true)

      emit('added')
      return
    }

    // A failed card is final, so clear the checkout and let the customer start again.
    if (result.status === 'Failed') {
      checkout.value = null
      error.value = result.message || __('We could not verify this card. Try another card.')
      return
    }

    message.value =
      result.message || __('Not confirmed yet. Finish adding the card, then check again.')
  })
}

const run = async (action: () => Promise<void>) => {
  working.value = true
  error.value = ''
  message.value = ''

  try {
    await action()
  } catch (exception) {
    error.value = getErrorMessage(exception)
  } finally {
    working.value = false
  }
}
</script>

<template>
  <section class="space-y-4">
    <div>
      <p v-if="step" class="text-p-sm text-ink-gray-5">{{ step }}</p>

      <h3 class="text-base-semibold text-ink-gray-8" :class="step && 'mt-1'">{{ __("Payment method") }}</h3>
    </div>

    <ErrorMessage :message="error" />

    <div v-if="methods.length > 1">
      <p class="mb-2 text-p-sm text-ink-gray-5">
        {{ __("Pay by") }}
      </p>

      <div class="grid gap-3 sm:grid-cols-2">
        <ChoiceTile
          v-for="option in methods"
          :key="option.value"
          :label="option.label"
          :hint="option.hint"
          :icon="option.icon"
          :image="option.image"
          :selected="method === option.value"
          :disabled="working || !!checkout"
          @click="method = option.value"
        />
      </div>
    </div>

    <div v-if="!gateways || visibleGateways.length !== 1">
      <p class="mb-2 text-p-sm text-ink-gray-5">{{ __("Pay through") }}</p>
      <PanelSkeleton v-if="!gateways && !error" kind="tiles" :rows="1" />
      <Button v-else-if="!gateways" :label="__('Try again')" @click="load" />

      <div v-else-if="!visibleGateways.length" class="space-y-2">
        <p class="text-p-sm text-ink-gray-5">
          {{ __(
              "No gateway is available for this payment type in your billing currency.",
            ) }}
        </p>

        <Button
          v-if="accountUrl"
          icon-right="lucide-arrow-up-right"
          :label="__('Manage account')"
          @click="openExternal(accountUrl)"
        />
      </div>

      <div v-else class="grid grid-cols-[repeat(auto-fit,minmax(11rem,1fr))] gap-3">
        <ChoiceTile
          v-for="option in visibleGateways"
          :key="option.name"
          :label="option.label"
          :hint="option.subtitle"
          :image="GATEWAY_LOGOS[option.adapter_key]"
          :selected="selected === option.name"
          :disabled="working || !!checkout"
          @click="selected = option.name"
        />
      </div>
    </div>

    <PhoneInput
      v-if="needsContact"
      v-model="contact"
      class="max-w-md"
      required
      :label="__('Phone')"
      :country="profile.country"
      :disabled="working"
      :error="contactError"
    />

    <p class="flex items-start gap-1.5 text-p-sm text-ink-gray-5">
      <span class="lucide-info mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      {{ paymentNote }}
    </p>

    <p v-if="message" class="flex items-start gap-1.5 text-p-sm text-ink-gray-5">
      <span class="lucide-info mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      {{ message }}
    </p>

    <PaymentTrustNote :gateway="gateway" />

    <div class="flex justify-end gap-2">
      <Button :disabled="working" @click="emit('close')" :label="__('Cancel')" />

      <template v-if="checkout">
        <Button @click="openExternal(checkout.checkout_url)" :label="__('Reopen checkout')" />
        <Button variant="solid" :loading="working" @click="check" :label="__('Check status')" />
      </template>

      <Button v-else variant="solid" :loading="working" :disabled="!canContinue" @click="start">
        {{ gateway ? __("Continue with {0}", [gateway.label]) : __("Continue") }}
      </Button>
    </div>
  </section>
</template>
