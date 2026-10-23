<script setup lang="ts">
import { useTranslation } from '../translation'
import { reconcilePaymentSetup, removePaymentMethod } from '@frappe/cloud-sdk/api'
import { Badge, Button, ErrorMessage, toast } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import AddPaymentCard from '../components/AddPaymentCard.vue'
import BillingProfileCard from '../components/BillingProfileCard.vue'
import ChangePlanCard from '../components/ChangePlanCard.vue'
import Panel from '../components/Panel.vue'
import TrialCard from '../components/TrialCard.vue'
import { openExternal } from '../external'
import { useErrorMessage, type Store } from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const flow = ref<'' | 'plan' | 'profile' | 'payment'>('')
// Billing details come first only when Central still needs them, so the steps count from there.
const stepCount = ref(1)
const removing = ref(false)
const removeError = ref('')
const paymentSetupError = ref('')

const load = async () => {
  paymentSetupError.value = ''
  store.loadAccountLinks()
  await store.loadBilling(true)

  try {
    const result = await reconcilePaymentSetup()
    if (result.activated.length) await store.loadBilling(true)
  } catch (exception) {
    paymentSetupError.value = getErrorMessage(exception)
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) load()
  },
  { immediate: true },
)

const billing = computed(() => store.state.billing)
const error = computed(() => store.state.billingError)
const loadFailed = computed(() => Boolean(error.value) && !billing.value)
const plan = computed(() => billing.value?.plan)
const trial = computed(() => billing.value?.trial)
const isAddingPayment = computed(() => flow.value === 'profile' || flow.value === 'payment')

const planSubtitle = computed(() => {
  if (plan.value?.subtitle) return plan.value.subtitle

  return Object.values(plan.value?.specs || {})
    .filter(Boolean)
    .join(' · ')
})

const startPayment = () => {
  removeError.value = ''
  stepCount.value = billing.value?.profile_complete ? 1 : 2
  flow.value = billing.value?.profile_complete ? 'payment' : 'profile'
}

const finishPayment = () => {
  flow.value = ''
  toast.success(__('Payment method added.'))
}

const removeCard = async () => {
  if (removing.value || !billing.value?.payment_method) return

  removing.value = true
  removeError.value = ''

  try {
    await removePaymentMethod(billing.value.payment_method.name)
    await store.loadBilling(true)
  } catch (exception) {
    removeError.value = getErrorMessage(exception)
  } finally {
    removing.value = false
  }
}

const changePlanUrl = computed(() => store.state.accountLinks?.change_plan)
</script>

<template>
  <Panel
    :title="__('Billing')"
    :description="__('Your plan and payment method.')"
    :loading="!billing && !error"
    :error="loadFailed ? error : ''"
    :error-title="__(`Couldn't load billing`)"
    @retry="load"
  >
    <div
      v-if="!plan"
      class="flex min-h-64 flex-col items-center justify-center rounded-6 border border-dashed border-outline-gray-3 px-6 py-12 text-center"
    >
      <div
        class="flex size-10 items-center justify-center rounded-6 bg-surface-gray-2 text-ink-gray-5"
      >
        <span class="lucide-wallet size-4" aria-hidden="true" />
      </div>

      <p class="mt-4 text-base-medium text-ink-gray-8">
        {{ __("Billing isn't available for this site yet") }}
      </p>

      <p class="mt-1 max-w-sm text-p-sm text-ink-gray-5">
        {{ __(
            "This site isn't connected to a billing account, or the connection isn't ready.",
          ) }}
      </p>

      <Button
        class="mt-5"
        icon-right="lucide-arrow-up-right"
        :disabled="!changePlanUrl"
        :label="__('View plans')"
        @click="openExternal(changePlanUrl)"
      />

      <ErrorMessage :message="store.state.accountLinksError" class="mt-2" />
    </div>

    <div v-else-if="billing" class="space-y-4">
      <ErrorMessage :message="loadFailed ? '' : error" />
      <ErrorMessage :message="paymentSetupError" />

      <TrialCard
        v-if="trial"
        :trial="trial"
        :plan-name="plan.name"
        :is-adding="isAddingPayment"
        @add-payment="startPayment"
      />

      <BillingProfileCard
        v-if="flow === 'profile'"
        :store="store"
        :step="__('Step 1 of 2')"
        @close="flow = ''"
        @saved="flow = 'payment'"
      />

      <AddPaymentCard
        v-else-if="flow === 'payment'"
        :store="store"
        :step="stepCount > 1 ? __('Step 2 of 2') : ''"
        @close="flow = ''"
        @added="finishPayment"
      />

      <ChangePlanCard
        v-if="flow === 'plan'"
        :store="store"
        @close="flow = ''"
      />

      <section
        v-else
        class="grid grid-cols-1 items-center gap-x-4 rounded-6 border border-outline-gray-2 p-5 sm:grid-cols-[minmax(0,1fr)_auto]"
      >
        <p class="flex items-center gap-2 text-base-semibold text-ink-gray-8">
          {{ plan.name ? __("{0} plan", [plan.name]) : __("Current plan") }}
          <Badge v-if="trial" :label="__('Free trial')" theme="blue" />
        </p>

        <p class="col-start-1 mt-1 text-sm text-ink-gray-5">{{ planSubtitle }}</p>

        <Button
          class="mt-3 justify-self-start sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0"
          :label="__('Change plan')"
          @click="flow = 'plan'"
        />

        <ErrorMessage :message="store.state.accountLinksError" class="mt-2 sm:col-span-2" />
      </section>

      <template v-if="!trial">
        <div class="grid grid-cols-[repeat(auto-fit,minmax(14rem,1fr))] gap-3">
          <section class="rounded-6 border border-outline-gray-2 p-5">
            <p class="text-p-sm text-ink-gray-5">{{ __("Estimated this cycle") }}</p>

            <p class="mt-1.5 text-2xl-semibold tabular-nums text-ink-gray-9">
              {{ billing.estimate?.amount ?? "-" }}
            </p>

            <p class="mt-1.5 text-p-sm text-ink-gray-5">{{ billing.estimate?.note }}</p>
          </section>

          <section class="rounded-6 border border-outline-gray-2 p-5">
            <p class="text-p-sm text-ink-gray-5">{{ __("Credit") }}</p>

            <p class="mt-1.5 text-2xl-semibold tabular-nums text-ink-gray-9">
              {{ billing.credit?.amount ?? "-" }}
            </p>

            <p
              class="mt-1.5 flex items-center gap-1.5 text-p-sm"
              :class="billing.credit?.warning ? 'text-ink-amber-6' : 'text-ink-gray-5'"
            >
              <span
                v-if="billing.credit?.warning"
                class="lucide-triangle-alert size-3.5 shrink-0"
                aria-hidden="true"
              />
              {{ billing.credit?.note }}
            </p>
          </section>
        </div>

        <section
          v-if="!isAddingPayment"
          class="grid grid-cols-1 items-center gap-x-4 rounded-6 border border-outline-gray-2 p-5 sm:grid-cols-[minmax(0,1fr)_auto]"
        >
          <p class="flex items-center gap-2 text-base-medium text-ink-gray-8">
            <span
              v-if="billing.payment_method"
              class="lucide-credit-card size-4 shrink-0 text-ink-gray-5"
              aria-hidden="true"
            />
            {{ billing.payment_method
                ? billing.payment_method.label
                : __("No payment method yet") }}
          </p>

          <p class="col-start-1 mt-0.5 text-p-sm text-ink-gray-5">
            {{ billing.payment_method
                ? __("Used for your monthly bill.")
                : __("Add a payment method to keep this site running.") }}
          </p>

          <Button
            v-if="billing.payment_method"
            class="mt-3 justify-self-start sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0"
            :loading="removing"
            :label="__('Remove')"
            @click="removeCard"
          />

          <Button
            v-else
            class="mt-3 justify-self-start sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0"
            variant="solid"
            icon-left="lucide-plus"
            :label="__('Add payment method')"
            @click="startPayment"
          />

          <ErrorMessage :message="removeError" class="mt-2 sm:col-span-2" />
        </section>
      </template>
    </div>
  </Panel>
</template>
