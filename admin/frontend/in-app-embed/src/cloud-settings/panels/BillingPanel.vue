<script setup lang="ts">
import { useTranslation } from '../translation'
import { reconcilePaymentSetup, removePaymentMethod } from '@frappe/cloud-sdk/api'
import type { PlanOption } from '@frappe/cloud-sdk'
import { Button, ErrorMessage, toast } from 'frappe-ui'
import { List } from 'frappe-ui/list'
import { computed, ref, watch } from 'vue'
import AddPaymentCard from '../components/AddPaymentCard.vue'
import BillingNotice from '../components/BillingNotice.vue'
import BillingRow from '../components/BillingRow.vue'
import BillingSummary from '../components/BillingSummary.vue'
import BillingProfileCard from '../components/BillingProfileCard.vue'
import ChangePlanCard from '../components/ChangePlanCard.vue'
import InvoiceList from '../components/InvoiceList.vue'
import Panel from '../components/Panel.vue'
import PlanMoveCard from '../components/PlanMoveCard.vue'
import TrialCard from '../components/TrialCard.vue'
import { openExternal } from '../external'
import { useInvoicePayment } from '../invoicePayment'
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
const replacingCard = ref('')
const replaceError = ref('')
const paymentSetupError = ref('')

const load = async () => {
  paymentSetupError.value = ''
  store.loadAccountLinks()
  await store.loadBilling(true)
  // A trial has no invoices yet.
  if (!store.isOnTrial()) store.loadInvoices()

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

const stepLabel = (step: Step) =>
  steps.value.length > 1 ? __('Step {0} of {1}', [steps.value.indexOf(step) + 1, steps.value.length]) : ''

// Kept here, because the trial is gone once the card converts it.
const chosenPlan = ref<PlanOption | null>(null)

const openSteps = (firstSteps: Step[]) => {
  replaceError.value = ''
  chosenPlan.value = null
  const needsPayment = !billing.value?.payment_method
  const needsProfile = needsPayment && !billing.value?.profile_complete
  steps.value = [...firstSteps]
  if (needsProfile) steps.value.push('profile')
  if (needsPayment) steps.value.push('payment')
  flow.value = steps.value[0]
}

const startUpgrade = () => openSteps(['plan'])

// The new card is added first, so a paid site is never left without a way to pay.
const changeCard = () => {
  replacingCard.value = billing.value?.payment_method?.name || ''
  if (!replacingCard.value) return openSteps([])

  replaceError.value = ''
  steps.value = ['payment']
  flow.value = 'payment'
}

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

  await removeReplacedCard()
  await store.loadBilling(true)
  if (chosenPlan.value && !billing.value?.trial) flow.value = 'moving'
}

const removeReplacedCard = async () => {
  const replaced = replacingCard.value
  replacingCard.value = ''
  if (!replaced) return

  try {
    await removePaymentMethod(replaced)
  } catch (exception) {
    replaceError.value = getErrorMessage(exception)
  }
}

// A pending move also shows after the dialog reopens, for example when the card was added in a new tab.
const isMoving = computed(
  () => flow.value === 'moving' || (!flow.value && Boolean(billing.value?.plan_change?.progress_url)),
)
const movingPlan = computed(() => chosenPlan.value?.title || billing.value?.plan_change?.plan || '')

const editDetails = () => {
  steps.value = ['profile']
  flow.value = 'profile'
}

const changePlan = () => {
  steps.value = []
  flow.value = 'plan'
}

const changePlanUrl = computed(() => store.state.accountLinks?.change_plan)

const invoicePayment = useInvoicePayment(async () => {
  await Promise.all([store.loadInvoices(), store.loadBilling(true)])
})
</script>

<template>
  <Panel
    :title="__('Billing')"
    :description="trial ? __('Your free trial and plan.') : __('Your plan, next payment and invoices.')"
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
        v-else-if="!isAddingPayment && trial"
        class="rounded-6 border border-outline-gray-1.5 px-4"
        :aria-label="__('Plan')"
      >
        <List :columns="['minmax(0,1fr)']">
          <BillingRow :label="__('Plan')" :value="__('Free trial')" :detail="plan.subtitle" />
        </List>
      </section>

      <template v-if="!trial && !flow && !isMoving">
        <BillingNotice
          :billing="billing"
          :invoices="store.state.invoices"
          :payment="invoicePayment"
          :billing-url="store.state.accountLinks?.billing"
          @change-card="changeCard"
        />

        <BillingSummary
          :billing="billing"
          @change-plan="changePlan"
          @change-card="changeCard"
          @edit-details="editDetails"
        />

        <ErrorMessage :message="replaceError" />

        <InvoiceList
          class="pt-3"
          :invoices="store.state.invoices"
          :error="store.state.invoicesError"
          :payment="invoicePayment"
          @retry="store.loadInvoices"
        />
      </template>
    </div>
  </Panel>
</template>
