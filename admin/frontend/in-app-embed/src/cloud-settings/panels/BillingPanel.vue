<script setup lang="ts">
import { Button, ErrorMessage } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import AddPaymentCard from '../components/AddPaymentCard.vue'
import BillingProfileCard from '../components/BillingProfileCard.vue'
import Panel from '../components/Panel.vue'
import { openExternal } from '../external'
import type { Store } from '../store'

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()
const store = props.store

const flow = ref('')
const removing = ref(false)
const removeError = ref('')
const openingChangePlan = ref(false)
const changePlanError = ref('')

const load = async () => {
  await store.loadBilling(true)

  try {
    await store.api.reconcilePaymentSetup()
    await store.loadBilling(true)
  } catch {}
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

const planSubtitle = computed(() => {
  if (plan.value?.subtitle) return plan.value.subtitle

  return Object.values(plan.value?.specs || {})
    .filter(Boolean)
    .join(' · ')
})

const startPayment = () => {
  removeError.value = ''
  flow.value = billing.value?.profile_complete ? 'payment' : 'profile'
}

const removeCard = async () => {
  if (removing.value) return

  removing.value = true
  removeError.value = ''

  try {
    await store.api.removePaymentMethod(billing.value.payment_method.name)
    await store.loadBilling(true)
  } catch (exception) {
    removeError.value = store.api.getErrorMessage(exception)
  } finally {
    removing.value = false
  }
}

const openChangePlan = async () => {
  if (openingChangePlan.value) return

  openingChangePlan.value = true
  changePlanError.value = ''

  try {
    const response = store.state.context?.account_url
      ? { url: store.state.context.account_url }
      : await store.api.getAccountUrl()

    if (!response?.url) throw new Error(__('Central is not configured.'))

    openExternal(response.url)
  } catch (exception) {
    changePlanError.value = store.api.getErrorMessage(exception)
  } finally {
    openingChangePlan.value = false
  }
}
</script>

<template>
  <Panel
    :title="__('Billing')"
    :description="__('Your plan, credit and payment method.')"
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
        :disabled="openingChangePlan"
        :label="openingChangePlan ? __('Opening…') : __('View plans')"
        @click="openChangePlan"
      />

      <ErrorMessage :message="changePlanError" class="mt-2" />
    </div>

    <div v-else class="space-y-4">
      <ErrorMessage :message="loadFailed ? '' : error" />

      <section
        class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-6 border border-outline-gray-2 p-5"
      >
        <p class="text-base-semibold text-ink-gray-8">
          {{ plan.name ? __("{0} plan", [plan.name]) : __("Current plan") }}
        </p>

        <p class="col-start-1 mt-1 text-sm text-ink-gray-5">{{ planSubtitle }}</p>

        <Button
          class="col-start-2 row-span-2 row-start-1"
          icon-right="lucide-arrow-up-right"
          :disabled="openingChangePlan"
          :label="openingChangePlan ? __('Opening…') : __('Change plan')"
          @click="openChangePlan"
        />

        <ErrorMessage :message="changePlanError" class="col-span-2 mt-2" />
      </section>

      <div class="grid grid-cols-[repeat(auto-fit,minmax(14rem,1fr))] gap-3">
        <section class="rounded-6 border border-outline-gray-2 p-5">
          <p class="text-p-sm text-ink-gray-5">{{ __("Estimated this cycle") }}</p>

          <p class="mt-1.5 text-2xl-semibold tabular-nums text-ink-gray-9">
            {{ billing.estimate?.amount ?? "—" }}
          </p>

          <p class="mt-1.5 text-p-sm text-ink-gray-5">{{ billing.estimate?.note }}</p>
        </section>

        <section class="rounded-6 border border-outline-gray-2 p-5">
          <p class="text-p-sm text-ink-gray-5">{{ __("Trial credit") }}</p>

          <p class="mt-1.5 text-2xl-semibold tabular-nums text-ink-gray-9">
            {{ billing.credit?.amount ?? "—" }}
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

      <BillingProfileCard
        v-if="flow === 'profile'"
        :store="store"
        @close="flow = ''"
        @saved="flow = 'payment'"
      />

      <AddPaymentCard v-else-if="flow === 'payment'" :store="store" @close="flow = ''" />

      <section
        v-else
        class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-6 border border-outline-gray-2 p-5"
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
              : __(
                  "You're on trial credit. Add a payment method to keep this site running after it.",
                ) }}
        </p>

        <Button
          v-if="billing.payment_method"
          class="col-start-2 row-span-2 row-start-1"
          :loading="removing"
          :label="__('Remove')"
          @click="removeCard"
        />

        <Button
          v-else
          class="col-start-2 row-span-2 row-start-1"
          variant="solid"
          icon-left="lucide-plus"
          :label="__('Add payment method')"
          @click="startPayment"
        />

        <ErrorMessage :message="removeError" class="col-span-2 mt-2" />
      </section>
    </div>
  </Panel>
</template>
