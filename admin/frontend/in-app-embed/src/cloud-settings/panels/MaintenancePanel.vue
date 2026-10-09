<script setup lang="ts">
import { useTranslation } from '../translation'
import { clearCache, migrate } from '@frappe/cloud-sdk/api'
import { Button, ErrorMessage, toast } from 'frappe-ui'
import { onBeforeUnmount, ref } from 'vue'
import Panel from '../components/Panel.vue'
import { useErrorMessage, type Store, settleTask } from '../store'

const __ = useTranslation()
const getErrorMessage = useErrorMessage()

interface Props {
  store: Store
}

defineProps<Props>()

const actions = [
  {
    key: 'clear-cache',
    title: __('Clear cache'),
    description: __('Drops cached pages and settings. Safe to run any time something looks stale.'),
    label: __('Clear cache'),
    done: __('Cache cleared.'),
    run: () => clearCache(),
  },
  {
    key: 'migrate',
    title: __('Run migrations'),
    description: __(
      'Applies pending database changes from your apps. The site can be slow while it runs.',
    ),
    label: __('Run migrations'),
    done: __('Migrations finished.'),
    run: () => migrate(),
  },
]

const running = ref('')
const error = ref('')

let gone = false

onBeforeUnmount(() => (gone = true))

const run = async (action: typeof actions[number]) => {
  running.value = action.key
  error.value = ''

  try {
    const { task_id } = await action.run()

    if (!(await settleTask(task_id, () => gone, __("Couldn't finish: {0}.", [action.title]), __)))
      return

    toast.success(action.done)
  } catch (exception) {
    error.value = getErrorMessage(exception)
  } finally {
    running.value = ''
  }
}
</script>

<template>
  <Panel :title="__('Maintenance')" :description="__('Housekeeping tasks for your site.')">
    <div class="divide-y divide-outline-gray-1 border-t border-outline-gray-1">
      <div
        class="flex flex-col items-start gap-4 py-5 sm:flex-row sm:items-center sm:gap-6"
        v-for="action in actions"
        :key="action.key"
      >
        <div class="min-w-0 flex-1">
          <h3 class="text-base-medium text-ink-gray-8">{{ action.title }}</h3>
          <p class="mt-1 text-base leading-5 text-ink-gray-6">{{ action.description }}</p>
        </div>
        <Button
          class="w-36 shrink-0"
          size="md"
          variant="outline"
          :loading="running === action.key"
          :disabled="Boolean(running) && running !== action.key"
          :label="action.label"
          @click="run(action)"
        />
      </div>
    </div>

    <ErrorMessage :message="error" class="mt-2" />
  </Panel>
</template>
