<template>
  <div class="mx-auto max-w-3xl">
    <div v-if="loading && !op" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <ErrorMessage v-else-if="error" class="mt-4" :message="error" />

    <template v-else-if="op">
      <div class="flex justify-between items-center gap-3">
        <div>
          <h1 class="font-semibold text-ink-gray-9 text-xl">{{ kindLabel(op.kind) }}</h1>
          <p class="mt-1 text-ink-gray-5 text-p-sm">Started {{ fmtDate(op.started_at) || '—' }}</p>
        </div>
        <MigrationStateBadge :state="op.state" />
      </div>

      <!-- Sites -->
      <div class="bg-surface-elevation-1 mt-4 divide-outline-gray-1 divide-y overflow-hidden">
        <div v-for="site in op.sites" :key="site.name" class="flex items-center gap-3 py-3">
          <span class="size-2 rounded-full shrink-0" :class="dotClass(site.migration_status)" />
          <span class="flex-1 font-medium text-ink-gray-9 text-base truncate">{{ site.name }}</span>
          <span class="text-ink-gray-5 text-p-sm">{{ site.migration_status }}</span>
        </div>
      </div>

      <!-- Failure + recovery -->
      <div v-if="isAttention" class="mt-6">
        <h2 class="font-semibold text-ink-gray-8 text-base">Recovery</h2>
        <p v-if="op.diagnosis?.message" class="mt-1 text-ink-red-5 text-p-sm">{{ op.diagnosis.message }}</p>
        <p v-if="op.diagnosis?.patch" class="mt-1 text-ink-gray-6 text-p-sm">
          Failing patch: <code>{{ op.diagnosis.patch }}</code>
        </p>

        <div class="flex flex-wrap gap-2 mt-3">
          <Button variant="solid" :loading="acting" @click="doRetry">Retry</Button>
          <Button v-if="op.can_revert" variant="outline" :loading="acting" @click="doRevert">
            Revert this update
          </Button>
          <Button v-if="op.diagnosis?.patch" variant="outline" theme="red" :loading="acting"
            @click="confirmSkip = true">
            Skip this patch
          </Button>
        </div>

        <pre v-if="op.diagnosis?.output_excerpt"
          class="bg-surface-gray-2 mt-4 p-3 rounded text-ink-gray-7 text-xs overflow-auto max-h-72">{{ op.diagnosis.output_excerpt }}</pre>
      </div>

      <p v-if="op.task_log_url" class="mt-6 text-p-sm">
        <a :href="op.task_log_url" class="text-ink-blue-5">View task log</a>
      </p>

      <Dialog v-model="confirmSkip" :options="{ title: 'Skip this patch permanently?' }">
        <template #body-content>
          <p class="text-ink-gray-6 text-p-sm">
            Skipping marks <code>{{ op.diagnosis?.patch }}</code> as completed for
            <b>{{ op.failed_site }}</b> without running it. This is permanent and cannot be undone.
            You must still choose Retry afterwards to continue the migration.
          </p>
        </template>
        <template #actions>
          <Button variant="solid" theme="red" :loading="acting" @click="doSkip">Skip patch</Button>
        </template>
      </Dialog>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Button, Dialog, ErrorMessage, LoadingText } from 'frappe-ui'
import { migrationsApi, isResolved, needsAttention } from '@/api/migrations'
import MigrationStateBadge from '@/components/migrations/MigrationStateBadge.vue'
import { kindLabel, fmtDate } from '@/utils/migrationFormat'

const props = defineProps({ operationId: { type: String, required: true } })

const op = ref(null)
const loading = ref(false)
const acting = ref(false)
const error = ref('')
const confirmSkip = ref(false)
let timer = null

const isAttention = computed(() => needsAttention(op.value))

async function load() {
  loading.value = true
  try {
    op.value = await migrationsApi.detail(props.operationId)
    error.value = ''
  } catch (e) {
    error.value = e?.message || 'Could not load this migration.'
  } finally {
    loading.value = false
    schedule()
  }
}

function schedule() {
  clearTimeout(timer)
  if (op.value && !isResolved(op.value) && !isAttention.value) {
    timer = setTimeout(load, 3000)
  }
}

async function runAction(action) {
  acting.value = true
  try {
    await action()
    await load()
  } catch (e) {
    error.value = e?.message || 'Action failed.'
  } finally {
    acting.value = false
  }
}

const doRetry = () => runAction(() => migrationsApi.retry(props.operationId))
const doRevert = () => runAction(() => migrationsApi.revert(props.operationId))
const doSkip = () => {
  confirmSkip.value = false
  return runAction(() => migrationsApi.bypassPatch(props.operationId, op.value.diagnosis.patch))
}

function dotClass(status) {
  return {
    success: 'bg-surface-green-3',
    failed: 'bg-surface-red-3',
    running: 'bg-surface-amber-3 animate-pulse',
    pending: 'bg-surface-gray-4',
  }[status] || 'bg-surface-gray-4'
}

onMounted(load)
onUnmounted(() => clearTimeout(timer))
</script>
