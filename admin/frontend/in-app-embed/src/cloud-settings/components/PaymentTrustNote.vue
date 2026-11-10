<script setup lang="ts">
import type { PaymentGateway } from '@frappe/cloud-sdk'
import { useTranslation } from '../translation'
import { GATEWAY_LOGOS } from './gatewayLogos'

const __ = useTranslation()

interface Props {
  gateway?: PaymentGateway
}

defineProps<Props>()
</script>

<template>
  <p class="flex items-start gap-1.5 text-p-sm text-ink-gray-5">
    <img
      v-if="gateway && GATEWAY_LOGOS[gateway.adapter_key]"
      :src="GATEWAY_LOGOS[gateway.adapter_key]"
      :alt="__('Secured by {0}', [gateway.label])"
      class="mt-0.5 size-3.5 shrink-0 rounded-2 object-contain"
    />
    <span v-else class="lucide-lock mt-0.5 size-3.5 shrink-0" aria-hidden="true" />

    {{ __("{0} collects your payment details. Frappe never sees them.", [gateway?.label || __("The payment gateway")]) }}
  </p>
</template>
