<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { AppTask } from '../store'
import { useTranslation } from '../translation'
import AppTaskCard from './AppTaskCard.vue'

const __ = useTranslation()

interface Item extends AppTask {
  name: string
  title: string
  logoUrl: string
  verb: 'install' | 'uninstall'
}

interface Props {
  items: Item[]
  /** Pilot's page for a task, or empty when the user can't open the server. */
  getTaskUrl: (taskId: string) => string
}

const props = defineProps<Props>()
const emit = defineEmits<{ retry: [item: Item]; dismiss: [name: string] }>()

const list = ref<HTMLElement>()
const runningTask = computed(() => props.items.find((item) => item.task?.status === 'running'))

// A new card opens above the app list, so bring it into view from wherever the user scrolled.
watch(
  () => props.items.map((item) => item.name),
  async (names, previous = []) => {
    const added = names.find((name) => !previous.includes(name))
    if (!added) return

    await nextTick()
    const card = list.value?.querySelector(`[data-task-app="${CSS.escape(added)}"]`)
    const isReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    card?.scrollIntoView({ block: 'nearest', behavior: isReduced ? 'auto' : 'smooth' })
  },
)
</script>

<template>
  <template v-if="items.length">
    <h3 class="mb-3 mt-6 text-base-semibold text-ink-gray-8">{{ __("In progress") }}</h3>

    <div ref="list" class="flex flex-col gap-2">
      <AppTaskCard
        v-for="item in items"
        :key="item.taskId"
        :data-task-app="item.name"
        :title="item.title"
        :logo-url="item.logoUrl"
        :verb="item.verb"
        :task-id="item.taskId"
        :task="item.task"
        :is-unreachable="item.isUnreachable"
        :log-url="getTaskUrl(item.taskId)"
        :waiting-for="runningTask?.title"
        @retry="emit('retry', item)"
        @dismiss="emit('dismiss', item.name)"
      />
    </div>
  </template>
</template>
