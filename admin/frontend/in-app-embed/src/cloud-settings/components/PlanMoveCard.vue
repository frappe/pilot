<script setup lang="ts">
import { useTranslation } from '../translation'
import { Button, LoadingIndicator } from 'frappe-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Store } from '../store'

const __ = useTranslation()

interface Props {
  store: Store
  /** The title of the plan the site moves to, when known. */
  plan?: string
}

const props = defineProps<Props>()

// Central queues the move in a background job, so it can take a few seconds to show.
const POLL_MS = 3000
const POLL_ATTEMPTS = 20

const progressUrl = computed(() => props.store.state.billing?.plan_change?.progress_url || '')
const isWaiting = ref(true)
let attempts = 0
let timer: number | undefined

const openProgress = () => window.location.assign(progressUrl.value)

const stop = () => {
  window.clearInterval(timer)
  isWaiting.value = false
}

watch(
  progressUrl,
  (url) => {
    if (url) {
      stop()
      openProgress()
      return
    }
    if (timer) return

    timer = window.setInterval(() => {
      if (++attempts > POLL_ATTEMPTS) return stop()
      props.store.loadBilling(true)
    }, POLL_MS)
  },
  { immediate: true },
)

onBeforeUnmount(stop)
</script>

<template>
  <section class="rounded-6 border border-outline-gray-1.5 p-5" role="status" aria-live="polite">
    <p class="flex items-center gap-2 text-base-semibold text-ink-gray-8">
      <span class="lucide-circle-check size-4 text-ink-green-3" aria-hidden="true" />
      {{ plan ? __("Upgrading to {0}", [plan]) : __("Upgrading your plan") }}
    </p>
    <p class="mt-1 text-p-sm text-ink-gray-6">
      {{ __("Billing has started, and full server access is on. Your site restarts in a few minutes while it moves to the new plan.") }}
    </p>

    <p v-if="isWaiting" class="mt-4 flex items-center gap-2 text-p-sm text-ink-gray-5">
      <LoadingIndicator class="size-4" />
      {{ __("Starting the move…") }}
    </p>
    <Button
      v-else-if="progressUrl"
      class="mt-4"
      variant="solid"
      icon-right="lucide-arrow-up-right"
      :label="__('Follow progress')"
      @click="openProgress"
    />
    <p v-else class="mt-4 text-p-sm text-ink-gray-5">
      {{ __("The move starts shortly. Your site comes back on its own when it is ready.") }}
    </p>
  </section>
</template>
