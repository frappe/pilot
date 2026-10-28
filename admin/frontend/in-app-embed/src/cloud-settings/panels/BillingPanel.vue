<script setup lang="ts">
import { useTranslation } from '../translation'
import { reconcilePaymentSetup, removePaymentMethod } from '@frappe/cloud-sdk/api'
import type { PlanOption } from '@frappe/cloud-sdk'
import { Badge, Button, ErrorMessage, toast } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import AddPaymentCard from '../components/AddPaymentCard.vue'
import BillingProfileCard from '../components/BillingProfileCard.vue'
import ChangePlanCard from '../components/ChangePlanCard.vue'
import Panel from '../components/Panel.vue'
import PlanMoveCard from '../components/PlanMoveCard.vue'
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

type Step = 'plan' | 'profile' | 'payment'

const flow = ref<'' | Step | 'moving'>('')
// The steps of the open flow. Billing details and the payment method show only when Central still needs them.
const steps = ref<Step[]>([])
const removing = ref(false)
const removeError = ref('')
const paymentSetupError = ref('')

const load = async () => {
  paymentSetupError.value = ''
  store.loadAccountLinks()
  await store.loadBilling(true)

  try {
    const result = await reconcilePaymentSetup()
    if (result.activated) await store.loadBilling(true)
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

const stepLabel = (step: Step) =>
  steps.value.length > 1 ? __('Step {0} of {1}', [steps.value.indexOf(step) + 1, steps.value.length]) : ''

// Kept here, because the trial is gone once the card converts it.
const chosenPlan = ref<PlanOption | null>(null)

const openSteps = (firstSteps: Step[]) => {
  removeError.value = ''
  chosenPlan.value = null
  const needsPayment = !billing.value?.payment_method
  const needsProfile = needsPayment && !billing.value?.profile_complete
  steps.value = [...firstSteps]
  if (needsProfile) steps.value.push('profile')
  if (needsPayment) steps.value.push('payment')
  flow.value = steps.value[0]
}

const startUpgrade = () => openSteps(['plan'])
const startPayment = () => openSteps([])

const nextStep = (current: Step) => {
  flow.value = steps.value[steps.value.indexOf(current) + 1] || ''
}

const onPlanChosen = async (plan: PlanOption) => {
  chosenPlan.value = plan
  if (!billing.value?.payment_method) return nextStep('plan')

  // A team whose card is already active converts as soon as the plan fits its spending limit.
  await store.loadBilling(true)
  flow.value = billing.value?.trial ? '' : 'moving'
}

const finishPayment = async () => {
  flow.value = ''
  toast.success(__('Payment method added.'))

  await store.loadBilling(true)
  if (chosenPlan.value && !billing.value?.trial) flow.value = 'moving'
}

// A pending move also shows after the dialog reopens, for example when the card was added in a new tab.
const isMoving = computed(
  () => flow.value === 'moving' || (!flow.value && Boolean(billing.value?.plan_change?.progress_url)),
)
const movingPlan = computed(() => chosenPlan.value?.title || billing.value?.plan_change?.plan || '')

const changePlan = () => {
  steps.value = []
  flow.value = 'plan'
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
        :is-upgrading="Boolean(flow)"
        @upgrade="startUpgrade"
      />

      <section
        v-if="chosenPlan && isAddingPayment"
        class="flex items-center gap-3 rounded-6 border border-outline-gray-1.5 px-4 py-3"
      >
        <div class="min-w-0 flex-1">
          <p class="text-sm text-ink-gray-5">{{ __("Upgrading to") }}</p>
          <p class="mt-0.5 text-base-medium text-ink-gray-8">
            {{ chosenPlan.title }}
            <span class="tabular-nums text-ink-gray-6">· {{ chosenPlan.price }}</span>
          </p>
        </div>

        <Button :label="__('Change plan')" @click="flow = 'plan'" />
      </section>

      <BillingProfileCard
        v-if="flow === 'profile'"
        :store="store"
        :step="stepLabel('profile')"
        @close="flow = ''"
        @saved="nextStep('profile')"
      />

      <AddPaymentCard
        v-else-if="flow === 'payment'"
        :store="store"
        :step="stepLabel('payment')"
        @close="flow = ''"
        @added="finishPayment"
      />

      <ChangePlanCard
        v-if="flow === 'plan'"
        :store="store"
        :step="stepLabel('plan')"
        @close="flow = ''"
        @chosen="onPlanChosen"
      />

      <PlanMoveCard v-else-if="isMoving" :store="store" :plan="movingPlan" />

      <section
        v-else-if="!isAddingPayment"
        class="grid grid-cols-1 items-center gap-x-4 rounded-6 border border-outline-gray-1.5 p-5 sm:grid-cols-[minmax(0,1fr)_auto]"
      >
        <p class="flex items-center gap-2 text-base-semibold text-ink-gray-8">
          <template v-if="trial">
            {{ __("Trial plan") }}
            <Badge :label="__('Free trial')" theme="blue" />
          </template>
          <template v-else>{{ plan.name ? __("{0} plan", [plan.name]) : __("Current plan") }}</template>
        </p>

        <p class="col-start-1 mt-1 text-sm text-ink-gray-5">{{ planSubtitle }}</p>

        <Button
          v-if="!trial"
          class="mt-3 justify-self-start sm:col-start-2 sm:row-span-2 sm:row-start-1 sm:mt-0"
          :label="__('Change plan')"
          @click="changePlan"
        />

        <ErrorMessage :message="store.state.accountLinksError" class="mt-2 sm:col-span-2" />
      </section>

      <template v-if="!trial && !flow && !isMoving">
        <section class="border rounded-6 border-outline-gray-1.5">
          <div class="grid sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-outline-gray-1.5">
            <div class="p-5">
              <p class="text-sm text-ink-gray-5">{{ __("Estimated this cycle") }}</p>

              <p class="mt-2 text-3xl-semibold tabular-nums text-ink-gray-9">
                {{ billing.estimate?.amount ?? "-" }}
              </p>

              <p class="mt-1.5 text-sm text-ink-gray-5">{{ billing.estimate?.note }}</p>
            </div>

            <div class="p-5">
              <p class="text-sm text-ink-gray-5">{{ __("Credit") }}</p>

              <p class="mt-2 text-3xl-semibold tabular-nums text-ink-gray-9">
                {{ billing.credit?.amount ?? "-" }}
              </p>

              <p
                class="flex items-center gap-1.5 mt-1.5 text-sm"
                :class="billing.credit?.warning ? 'text-ink-amber-6' : 'text-ink-gray-5'"
              >
                <span
                  v-if="billing.credit?.warning"
                  class="lucide-triangle-alert size-3.5 shrink-0"
                  aria-hidden="true"
                />
                {{ billing.credit?.note }}
              </p>
            </div>
          </div>

          <div
            v-if="!isAddingPayment"
            class="flex items-center gap-3 px-5 py-4 border-t border-outline-gray-1.5"
          >
            <span class="grid place-items-center bg-surface-gray-2 rounded-6 size-9 shrink-0 text-ink-gray-6">
              <span class="lucide-credit-card size-4" aria-hidden="true" />
            </span>

            <div class="flex-1 min-w-0">
              <p class="text-base-medium text-ink-gray-8 truncate">
                {{ billing.payment_method ? billing.payment_method.label : __("No payment method yet") }}
              </p>

              <p class="mt-0.5 text-sm text-ink-gray-5">
                {{ billing.payment_method
                    ? __("Used for your monthly bill.")
                    : __("Add a payment method to keep this site running.") }}
              </p>
            </div>

            <Button
              v-if="billing.payment_method"
              :loading="removing"
              :label="__('Remove')"
              @click="removeCard"
            />

            <Button
              v-else
              variant="solid"
              icon-left="lucide-plus"
              :label="__('Add')"
              @click="startPayment"
            />
          </div>
        </section>

        <ErrorMessage :message="removeError" />
      </template>
    </div>
  </Panel>
</template>
