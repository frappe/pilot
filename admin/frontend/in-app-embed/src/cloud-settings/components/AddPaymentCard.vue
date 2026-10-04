<script setup lang="ts">
import { Button, ErrorMessage, Skeleton, TextInput } from 'frappe-ui'
import { computed, onMounted, ref, watch } from 'vue'
import RazorpayLogo from '../assets/Razorpay-1.svg?inline'
import StripeLogo from '../assets/Stripe.svg?inline'
import UpiLogo from '../assets/UPI-1.svg?inline'
import { openExternal } from '../external'
import type { Store } from '../store'

interface Props {
  store: Store
}

const props = defineProps<Props>()
const emit = defineEmits(['close'])
const store = props.store

const GATEWAY_LOGO = { Stripe: StripeLogo, Razorpay: RazorpayLogo }
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

const gateways = ref(null)
const method = ref('Card')
const selected = ref('')
const contact = ref('')
const checkout = ref(null)
const message = ref('')
const working = ref(false)
const error = ref('')

const visibleGateways = computed(() =>
  (gateways.value || []).filter(
    (g) => method.value !== 'UPI Autopay' || g.adapter_key === 'Razorpay',
  ),
)

const gateway = computed(() => visibleGateways.value.find((g) => g.name === selected.value))

const needsContact = computed(
  () => method.value === 'Card' && gateway.value?.adapter_key === 'Razorpay',
)

const canContinue = computed(
  () =>
    Boolean(gateway.value) &&
    !working.value &&
    (!needsContact.value || Boolean(contact.value.trim())),
)

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
    gateways.value = await store.api.getPaymentGateways()
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  }
}

onMounted(load)

const start = () => {
  if (!canContinue.value) return

  return gateway.value.adapter_key === 'Razorpay' ? startRazorpay() : startStripe()
}

const startStripe = async () => {
  await run(async () => {
    checkout.value = await store.api.createPaymentMethodCheckout(
      window.location.href,
      selected.value,
    )

    message.value = __('Checkout opened in a new tab. Add your card there, then check its status.')

    openExternal(checkout.value.checkout_url)
  })
}

const startRazorpay = async () => {
  working.value = true
  error.value = ''
  message.value = ''

  try {
    const handles = await store.api.addPaymentMethod(
      method.value,
      selected.value,
      contact.value.trim() || null,
    )

    await loadRazorpay()
    openRazorpayCheckout(handles)
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
    working.value = false
  }
}

const openRazorpayCheckout = (handles) => {
  const rzp = new window.Razorpay({
    key: handles.key_id,
    order_id: handles.order_id,
    customer_id: handles.customer_id,
    recurring: handles.recurring ? 1 : undefined,
    name: __('Frappe Cloud'),
    description:
      method.value === 'UPI Autopay' ? __('Set up UPI Autopay') : __('Save card for billing'),
    prefill: handles.prefill || {},
    handler: (response) => confirmRazorpay(handles.payment_method, response),
    modal: {
      ondismiss: () => {
        working.value = false
        message.value = __('Setup cancelled.')
      },
    },
  })

  rzp.on('payment.failed', (response) => {
    error.value = response?.error?.description || __('Authorisation failed.')
    working.value = false
  })

  rzp.open()
}

const confirmRazorpay = async (paymentMethod, response) => {
  await run(async () => {
    const result = await store.api.confirmPaymentMethod({
      payment_method: paymentMethod,
      razorpay_payment_id: response.razorpay_payment_id,
      razorpay_order_id: response.razorpay_order_id,
      razorpay_signature: response.razorpay_signature,
    })

    if (result.status === 'Active') {
      await store.loadBilling(true)

      emit('close')
      return
    }

    error.value = __('Saved but not active ({0}).', [result.status])
  })
}

const loadRazorpay = () => {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve()

    const script = document.createElement('script')

    script.src = RAZORPAY_SDK
    script.onload = resolve
    script.onerror = () => reject(new Error(__('Could not load Razorpay Checkout.')))

    document.body.appendChild(script)
  })
}

const check = async () => {
  await run(async () => {
    const result = await store.api.confirmPaymentMethodCheckout(checkout.value.reference)

    if (result.active) {
      await store.loadBilling(true)

      emit('close')
      return
    }

    message.value =
      result.message || __('Not confirmed yet — finish adding the card, then check again.')
  })
}

const run = async (action) => {
  working.value = true
  error.value = ''
  message.value = ''

  try {
    await action()
  } catch (exception) {
    error.value = store.api.getErrorMessage(exception)
  } finally {
    working.value = false
  }
}

const tileClass = (isSelected) => [
  'grid w-full grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 rounded-6 border p-3.5 text-left disabled:opacity-60',
  isSelected ? 'border-outline-gray-4' : 'border-outline-gray-2 hover:border-outline-gray-3',
]
</script>

<template>
  <section class="space-y-4 rounded-6 border border-outline-gray-2 p-5">
    <h2 class="text-base-semibold text-ink-gray-8">
      {{ __("Add payment method") }}
    </h2>

    <ErrorMessage :message="error" />

    <div>
      <p class="mb-2 text-p-sm text-ink-gray-5">
        {{ __("Choose a payment method") }}
      </p>

      <div class="grid gap-3 sm:grid-cols-2">
        <button
          v-for="option in METHODS"
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

    <div>
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

    <TextInput
      v-if="needsContact"
      v-model="contact"
      :label="__('Phone')"
      placeholder="+91 98765 43210"
      :disabled="working"
      class="max-w-xs"
    />

    <p class="flex items-center gap-1.5 text-p-sm text-ink-gray-5">
      <template v-if="message">{{ message }}</template>

      <template v-else>
        <span class="lucide-lock size-3.5 shrink-0" aria-hidden="true" />
        {{ __(
            "The gateway collects your card securely — this site never sees it.",
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
