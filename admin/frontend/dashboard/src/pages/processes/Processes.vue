<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button, ErrorMessage, toast } from 'frappe-ui'

import ActionDialog from '@/components/common/ActionDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ListRowSkeleton from '@/components/common/ListRowSkeleton.vue'
import ProcessBlockedNotice from '@/components/processes/ProcessBlockedNotice.vue'
import ProcessDeclarationDialog from '@/components/processes/ProcessDeclarationDialog.vue'
import ProcessGroup from '@/components/processes/ProcessGroup.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'

import { processesApi } from '@/api/processes'
import { useIsMobile } from '@/composables/common/useIsMobile'
import { useProcesses } from '@/composables/processes/useProcesses'

const isMobile = useIsMobile()
const { groups, blocked, loading, refreshing, error, paused, load, refresh, setPaused } =
  useProcesses()

const restartTarget = ref(null)
const showRestart = ref(false)
const restarting = ref(false)
const restartError = ref('')

const inspected = ref(null)
const showDeclaration = ref(false)

// Bench first, then apps alphabetically - the platform is what an operator scans for.
const orderedGroups = computed(() =>
  [...groups.value].sort((a, b) => {
    if (a.source === 'bench') return -1
    if (b.source === 'bench') return 1
    return a.source.localeCompare(b.source)
  }),
)

const totalProcesses = computed(() =>
  groups.value.reduce((count, group) => count + group.processes.length, 0),
)

const buttonSize = computed(() => (isMobile.value ? 'md' : 'sm'))

const askRestart = (process = null) => {
  restartTarget.value = process
  restartError.value = ''
  showRestart.value = true
}

const inspect = (process) => {
  inspected.value = process
  showDeclaration.value = true
}

const restartDialog = computed(() => {
  const process = restartTarget.value
  if (!process)
    return {
      title: 'Restart bench processes',
      subject: {
        icon: 'lucide-server',
        label: 'All bench processes',
        description: 'Web, realtime, workers and every app-declared process.',
      },
      warning: {
        title: 'The bench is briefly unavailable',
        message: 'Requests fail until the processes come back up.',
      },
    }
  return {
    title: 'Restart process',
    subject: {
      icon: 'lucide-cpu',
      label: process.name,
      description: process.declaration
        ? 'Setup hooks run again on start.'
        : 'The process stops and starts again.',
    },
    warning: null,
  }
})

const confirmRestart = async () => {
  restarting.value = true
  restartError.value = ''
  try {
    const process = restartTarget.value
    if (process) await processesApi.restart(process.name)
    else await processesApi.restartWorkload()
    showRestart.value = false
    toast.success(process ? `Restarting ${process.name}` : 'Restarting bench processes')
    await load()
  } catch (caught) {
    restartError.value = caught.message || 'Could not restart.'
  } finally {
    restarting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <StickyToolbar class="flex items-center gap-2 py-2 md:py-3">
      <Button
        :size="buttonSize"
        icon-left="lucide-refresh-cw"
        :disabled="loading || Boolean(blocked)"
        @click="askRestart()"
      >
        Restart bench
      </Button>

      <Button
        class="ml-auto"
        :size="buttonSize"
        :icon="paused ? 'lucide-play' : 'lucide-pause'"
        :label="paused ? 'Resume live updates' : 'Pause live updates'"
        :tooltip="paused ? 'Resume live updates' : 'Pause live updates'"
        @click="setPaused(!paused)"
      />

      <Button
        :size="buttonSize"
        icon="lucide-rotate-cw"
        label="Refresh"
        tooltip="Refresh"
        :loading="refreshing"
        @click="refresh"
      />
    </StickyToolbar>

    <div v-if="loading" class="-mx-3 mt-4">
      <ListRowSkeleton v-for="index in 6" :key="index" :index="index - 1" />
    </div>

    <ProcessBlockedNotice
      v-else-if="blocked"
      class="mt-4"
      :app="blocked.app"
      :message="blocked.message"
    />

    <div v-else-if="error" class="mt-4">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="totalProcesses" class="space-y-8 mt-4">
      <ProcessGroup
        v-for="group in orderedGroups"
        :key="group.source"
        :source="group.source"
        :processes="group.processes"
        @restart="askRestart"
        @inspect="inspect"
      />
    </div>

    <EmptyState
      v-else
      class="mt-8"
      icon="lucide-cpu"
      title="Nothing running"
      description="Web, workers and any process an installed app declares will appear here."
    />

    <ActionDialog
      v-model:open="showRestart"
      :title="restartDialog.title"
      :subject="restartDialog.subject"
      :warning="restartDialog.warning"
      :error="restartError"
      :loading="restarting"
      confirm-label="Restart"
      @confirm="confirmRestart"
    />

    <ProcessDeclarationDialog v-model:open="showDeclaration" :process="inspected" />
  </div>
</template>
