<script setup lang="ts">
import { useTranslation } from '../translation'
import {
  addPaymentMethod,
  confirmPaymentMethod,
  confirmPaymentMethodCheckout,
  createPaymentMethodCheckout,
  getPaymentGateways,
} from '@frappe/cloud-sdk/api'
import { Button, ErrorMessage, Skeleton, TextInput } from 'frappe-ui'
import type { PaymentCheckout, PaymentGateway, PaymentMethodSetup } from '@frappe/cloud-sdk'
import { computed, onMounted, ref, watch } from 'vue'
import RazorpayLogo from '../assets/Razorpay-1.svg?inline'
import StripeLogo from '../assets/Stripe.svg?inline'
import UpiLogo from '../assets/UPI-1.svg?inline'
import { openExternal } from '../external'
import { useErrorMessage, type Store } from '../store'

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

const GATEWAY_LOGO: Record<string, string> = { Stripe: StripeLogo, Razorpay: RazorpayLogo }
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

const needsContact = computed(
  () => method.value === 'Card' && gateway.value?.adapter_key === 'Razorpay',
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
    gateways.value = await getPaymentGateways()
  } catch (exception) {
    error.value = getErrorMessage(exception)
  }
}

onMounted(load)

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
        working.value = false
        message.value = __('Setup cancelled.')
      },
    },
  })

  rzp.on('payment.failed', (response) => {
    error.value = response?.error?.description || __('Your bank did not approve this. Try again or use another card.')
    working.value = false
  })

  rzp.open()
}

const confirmRazorpay = async (paymentMethod: string, response: RazorpayResponse) => {
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

const tileClass = (isSelected: boolean) => [
  'grid w-full grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 rounded-6 border p-3.5 text-left disabled:opacity-60',
  isSelected ? 'border-outline-gray-4' : 'border-outline-gray-2 hover:border-outline-gray-3',
]
</script>

<template>
  <section class="space-y-4 rounded-6 border border-outline-gray-2 p-5">
    <div>
      <p v-if="step" class="text-p-sm text-ink-gray-5">{{ step }}</p>

      <h3 class="text-base-semibold text-ink-gray-8" :class="step && 'mt-1'">
        {{ __("Payment method") }}
      </h3>
    </div>

    <ErrorMessage :message="error" />

    <div v-if="methods.length > 1">
      <p class="mb-2 text-p-sm text-ink-gray-5">
        {{ __("Pay by") }}
      </p>

      <div class="grid gap-3 sm:grid-cols-2">
        <button
          v-for="option in methods"
          :key="option.value"
          type="button"
          :class="tileClass(method === option.value)"
          :disabled="working || !!checkout"
          @click="method = option.value"
        >
          <span class="row-span-2 mt-0.5 flex size-6 items-center justify-center">
            <span v-if="option.icon" :class="[option.icon, 'size-5 text-ink-gray-7']" />

            <img
              v-else
              :src="option.image"
              :alt="option.label"
              class="max-h-full max-w-full object-contain"
            />
          </span>

          <span class="text-base-semibold text-ink-gray-8">
            {{ option.label }}
          </span>

          <span class="col-start-2 text-p-sm text-ink-gray-5">
            {{ option.hint }}
          </span>
        </button>
      </div>
    </div>

    <div v-if="!gateways || visibleGateways.length !== 1">
      <p class="mb-2 text-p-sm text-ink-gray-5">{{ __("Pay through") }}</p>
      <Skeleton v-if="!gateways && !error" class="h-16 rounded-6" />
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
        <button
          v-for="option in visibleGateways"
          :key="option.name"
          type="button"
          :class="tileClass(selected === option.name)"
          :disabled="working || !!checkout"
          @click="selected = option.name"
        >
          <span
            v-if="GATEWAY_LOGO[option.adapter_key]"
            class="row-span-2 mt-0.5 flex size-6 items-center justify-center"
          >
            <img
              :src="GATEWAY_LOGO[option.adapter_key]"
              :alt="option.label"
              class="max-h-full max-w-full object-contain"
              :class="{ 'rounded-4': option.adapter_key === 'Stripe' }"
            />
          </span>

          <span
            v-else
            class="row-span-2 flex size-8 items-center justify-center rounded-6 bg-surface-gray-3 text-p-sm-bold text-ink-gray-8"
          >
            {{ (option.label || "?").charAt(0) }}
          </span>

          <span class="text-base-medium text-ink-gray-8">
            {{ option.label }}
          </span>

          <span class="col-start-2 text-p-sm text-ink-gray-5">
            {{ option.subtitle }}
          </span>
        </button>
      </div>
    </div>

    <div v-if="needsContact" class="max-w-xs">
      <TextInput
        v-model="contact"
        type="tel"
        autocomplete="tel"
        :label="__('Phone')"
        placeholder="+91 98765 43210"
        :disabled="working"
        :aria-invalid="Boolean(contactError) || undefined"
      />
      <ErrorMessage :message="contactError" class="mt-1" />
    </div>

    <p class="flex items-center gap-1.5 text-p-sm text-ink-gray-5">
      <template v-if="message">{{ message }}</template>

      <template v-else>
        <span class="lucide-lock size-3.5 shrink-0" aria-hidden="true" />
        {{ __(
            "{0} collects your payment details. This site never sees them.",
            [gateway?.label || __("The payment gateway")],
          ) }}
      </template>
    </p>

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
