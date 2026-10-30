<script setup lang="ts">
import {
  Alert,
  Button,
  Dialog,
  ErrorMessage,
  Progress,
  Slider,
  Spinner,
  Switch,
  TextInput,
  Tooltip,
} from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { databaseApi } from '@/api/database'
import BinlogSettingsDialog from '@/components/settings/BinlogSettingsDialog.vue'
import MemoryRing from '@/components/settings/MemoryRing.vue'
import type { DatabaseActionCapabilities, DatabaseCapabilities } from '@/types/database'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

// The five capability payloads share available/reason and differ past that, so one
// reader type keeps them and the unavailable fallback interchangeable.
interface ActionCapability {
  available: boolean
  reason: string
  requires_restart?: boolean
  enabled?: boolean | null
  current?: number | null
  min?: number | null
  max?: number | null
  recommended?: number | null
  current_mb?: number | null
  min_mb?: number | null
  max_mb?: number | null
  recommended_mb?: number | null
  dynamic_max_mb?: number | null
  unit?: 'MB'
}

interface ConfirmationBase {
  title: string
  buttonLabel: string
  message: string
  idempotencyKey: string
}

interface RestartConfirmation extends ConfirmationBase {
  action: 'restart'
}

interface PerformanceSchemaConfirmation extends ConfirmationBase {
  action: 'performance_schema'
  enabled: boolean
}

interface PendingSizing {
  action: 'innodb_buffer_pool_size' | 'max_connections'
  title: string
  inputLabel: string
  current: number | null
  recommended: number | null
  min: number | null
  max: number | null
  dynamicMax: number | null
  unit: string
  idempotencyKey: string
}

const router = useRouter()

const loading = ref(false)
const capabilities = ref<DatabaseCapabilities | null>(null)
const error = ref('')
const actionError = ref('')
const activeAction = ref('')
const binlogsOpen = ref(false)
const confirmation = ref<RestartConfirmation | PerformanceSchemaConfirmation | null>(null)
const sizingAction = ref<PendingSizing | null>(null)
const sizingValue = ref<number | string>('')

const confirmationOpen = computed({
  get: () => Boolean(confirmation.value),
  set: (value) => {
    if (!value && !activeAction.value) confirmation.value = null
  },
})
const confirmationTitle = computed(() => confirmation.value?.title || 'Database action')
const sizingValidationError = computed(() => {
  const pending = sizingAction.value
  if (!pending) return ''

  const value = sizingValue.value

  if (typeof value !== 'number' || !Number.isInteger(value)) return 'Enter a whole number.'
  if (
    (pending.min !== null && value < pending.min) ||
    (pending.max !== null && value > pending.max)
  ) {
    return `Enter a value between ${pending.min} and ${pending.max}.`
  }
  return ''
})
const sizingRequiresRestart = computed(() => {
  const pending = sizingAction.value
  if (pending?.action !== 'innodb_buffer_pool_size') return false

  return (
    pending.dynamicMax !== null &&
    Number.isFinite(pending.dynamicMax) &&
    typeof sizingValue.value === 'number' &&
    sizingValue.value > pending.dynamicMax
  )
})
const sizingUnchanged = computed(
  () => sizingAction.value !== null && sizingValue.value === sizingAction.value.current,
)
const showManagedMariaDBDisclaimer = computed(
  () =>
    capabilities.value !== null &&
    (capabilities.value.engine !== 'mariadb' || !capabilities.value.managed),
)
const action = (name: keyof DatabaseActionCapabilities): ActionCapability => {
  return (
    capabilities.value?.actions?.[name] || {
      available: false,
      reason: 'Database capabilities are unavailable.',
    }
  )
}

const formatMegabytes = (megabytes: number) => {
  if (megabytes < 1024) return { amount: String(megabytes), unit: 'MB' }

  return { amount: String(Number((megabytes / 1024).toFixed(1))), unit: 'GB' }
}

const cancelSizing = () => {
  if (activeAction.value) return

  sizingAction.value = null
  actionError.value = ''
}

const idempotencyKey = (actionName: string) => {
  const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)
  return `database-${actionName}-${Date.now()}-${random}`
}

const confirmRestart = () => {
  actionError.value = ''
  confirmation.value = {
    action: 'restart',
    title: 'Restart MariaDB',
    buttonLabel: 'Restart MariaDB',
    message:
      'Sites, web requests, workers, and scheduled jobs may briefly lose their database connection.',
    idempotencyKey: idempotencyKey('restart'),
  }
}

const confirmPerformanceSchema = (enabled: boolean) => {
  if (action('performance_schema').enabled === enabled) return
  actionError.value = ''
  confirmation.value = {
    action: 'performance_schema',
    enabled,
    title: `${enabled ? 'Enable' : 'Disable'} Performance Schema`,
    buttonLabel: `${enabled ? 'Enable' : 'Disable'} and restart`,
    message: `Pilot will ${enabled ? 'enable' : 'disable'} Performance Schema and restart MariaDB. Sites, web requests, workers, and scheduled jobs may briefly lose their database connection.`,
    idempotencyKey: idempotencyKey(`performance-${enabled ? 'on' : 'off'}`),
  }
}

const openSizingAction = (actionName: 'innodb_buffer_pool_size' | 'max_connections') => {
  actionError.value = ''
  memorySplitOpen.value = false
  const capability = action(actionName)
  if (!capability.available) return
  if (actionName === 'innodb_buffer_pool_size') {
    sizingValue.value = capability.current_mb ?? ''
    sizingAction.value = {
      action: actionName,
      title: 'Update InnoDB Buffer Pool Size',
      inputLabel: 'Buffer Pool size (MB)',
      current: capability.current_mb ?? null,
      recommended: capability.recommended_mb ?? null,
      min: capability.min_mb ?? null,
      max: capability.max_mb ?? null,
      dynamicMax: capability.dynamic_max_mb ?? null,
      unit: 'MB',
      idempotencyKey: idempotencyKey('innodb-buffer-pool-size'),
    }
    return
  }
  sizingValue.value = capability.current ?? ''
  sizingAction.value = {
    action: actionName,
    title: 'Update Max DB Connections',
    inputLabel: 'Maximum connections',
    current: capability.current ?? null,
    recommended: capability.recommended ?? null,
    min: capability.min ?? null,
    max: capability.max ?? null,
    dynamicMax: null,
    unit: '',
    idempotencyKey: idempotencyKey('max-connections'),
  }
}

const runConfirmedAction = async () => {
  if (!confirmation.value || activeAction.value) return
  const pending = confirmation.value
  activeAction.value = pending.action
  actionError.value = ''
  try {
    const task =
      pending.action === 'restart'
        ? await databaseApi.quickActions.restart(pending.idempotencyKey)
        : await databaseApi.quickActions.setPerformanceSchema(
            pending.enabled,
            pending.idempotencyKey,
          )
    confirmation.value = null
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    actionError.value = errorMessage(e, 'Could not start the database action.')
  } finally {
    activeAction.value = ''
  }
}

const runSizingAction = async () => {
  if (
    !sizingAction.value ||
    sizingValidationError.value ||
    sizingUnchanged.value ||
    activeAction.value
  )
    return
  const pending = sizingAction.value
  const size = sizingValue.value
  if (typeof size !== 'number') return

  activeAction.value = pending.action
  actionError.value = ''
  try {
    const task =
      pending.action === 'innodb_buffer_pool_size'
        ? await databaseApi.quickActions.setInnoDBBufferPoolSize(size, pending.idempotencyKey)
        : await databaseApi.quickActions.setMaxConnections(size, pending.idempotencyKey)
    sizingAction.value = null
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    actionError.value = errorMessage(e, 'Could not start the database action.')
  } finally {
    activeAction.value = ''
  }
}

const memoryShare = computed(() => capabilities.value?.actions.memory_share ?? null)
const memorySplitOpen = ref(false)
const memorySplitPercent = ref(50)
const memorySplitIdempotencyKey = ref('')

const formatGB = (mb: number) => `${(mb / 1024).toFixed(1)} GB`
const sharePercent = (share: number) => Math.round(share * 100)

const editStat = (edit: () => void) => {
  sizingAction.value = null
  memorySplitOpen.value = false
  actionError.value = ''

  edit()
}

const memoryBreakdown = computed(() => {
  const share = memoryShare.value

  if (share?.current == null || !share.total_memory_mb) return null

  const database = sharePercent(share.current)

  return {
    database,
    databaseSize: formatGB((share.total_memory_mb * database) / 100),
    appsSize: formatGB((share.total_memory_mb * (100 - database)) / 100),
  }
})

const stats = computed(() => {
  const bufferPool = action('innodb_buffer_pool_size')
  const connections = action('max_connections')
  const databaseLimit = memoryBreakdown.value
    ? ((memoryShare.value?.total_memory_mb ?? 0) * memoryBreakdown.value.database) / 100
    : 0

  return [
    {
      key: 'innodb_buffer_pool_size',
      label: 'Buffer pool',
      available: bufferPool.available,
      ...(bufferPool.current_mb == null
        ? { amount: '-', unit: '' }
        : formatMegabytes(bufferPool.current_mb)),
      note: databaseLimit ? `of ${formatGB(databaseLimit)}` : '',
      percent:
        databaseLimit && bufferPool.current_mb != null
          ? Math.min((bufferPool.current_mb / databaseLimit) * 100, 100)
          : null,
      edit: () => openSizingAction('innodb_buffer_pool_size'),
    },
    {
      key: 'max_connections',
      label: 'Max connections',
      available: connections.available,
      amount: connections.current == null ? '-' : String(connections.current),
      unit: '',
      note: connections.recommended == null ? '' : `${connections.recommended} advised`,
      percent:
        connections.current != null && connections.recommended
          ? Math.min((connections.current / connections.recommended) * 100, 100)
          : null,
      edit: () => openSizingAction('max_connections'),
    },
  ]
})

const memoryRing = computed(() => {
  const total = memoryShare.value?.total_memory_mb ?? 0
  const percent = memoryBreakdown.value?.database ?? 0

  return {
    percent,
    apps: formatGB((total * (100 - percent)) / 100),
    bufferPool: stepFor(percent)?.innodb_buffer_pool_mb,
  }
})

const insight = computed(() => {
  const { current, recommended } = action('max_connections')

  if (current == null || recommended == null || current === recommended) return ''

  const direction = current < recommended ? 'below' : 'above'

  return `Max connections is ${Math.abs(recommended - current)} ${direction} the advised ${recommended}.`
})

const editNote = computed(() => {
  const pending = sizingAction.value

  if (!pending) return { text: '', class: '' }

  const label = stats.value.find((stat) => stat.key === pending.action)?.label

  if (sizingValidationError.value)
    return { text: `${label} · use ${pending.min} to ${pending.max}`, class: 'text-ink-red-5' }
  if (actionError.value) return { text: actionError.value, class: 'text-ink-red-5' }
  if (sizingRequiresRestart.value)
    return { text: `${label} · restarts MariaDB`, class: 'text-ink-amber-6' }

  return { text: `${label} · ${pending.recommended} advised`, class: 'text-ink-gray-5' }
})

const memorySplitSlider = computed({
  get: () => [memorySplitPercent.value],
  set: ([percent]: number[]) => {
    memorySplitPercent.value = percent
  },
})

const stepFor = (percent: number) => {
  const steps = memoryShare.value?.steps ?? []
  const distance = (share: number) => Math.abs(sharePercent(share) - percent)

  return steps.reduce<(typeof steps)[number] | null>(
    (closest, step) =>
      !closest || distance(step.share) < distance(closest.share) ? step : closest,
    null,
  )
}

const memorySplitStep = computed(() => stepFor(memorySplitPercent.value))

const memorySplitSides = computed(() => {
  const total = memoryShare.value?.total_memory_mb ?? 0
  const database = memorySplitPercent.value

  return [
    { label: 'Database', percent: database, size: formatGB((total * database) / 100) },
    {
      label: 'Apps, workers and Redis',
      percent: 100 - database,
      size: formatGB((total * (100 - database)) / 100),
    },
  ]
})

const openMemorySplit = () => {
  const capability = memoryShare.value
  if (!capability?.available || capability.current == null) return
  actionError.value = ''
  memorySplitPercent.value = sharePercent(capability.current)
  memorySplitIdempotencyKey.value = idempotencyKey('memory-share')
  memorySplitOpen.value = true
}

const applyMemorySplit = async () => {
  if (activeAction.value) return
  activeAction.value = 'memory_share'
  actionError.value = ''
  try {
    const task = await databaseApi.quickActions.setMemoryShare(
      memorySplitPercent.value / 100,
      memorySplitIdempotencyKey.value,
    )
    memorySplitOpen.value = false
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    actionError.value = errorMessage(e, 'Could not change the memory split.')
  } finally {
    activeAction.value = ''
  }
}

const load = async () => {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    capabilities.value = await databaseApi.quickActions.capabilities()
  } catch (e) {
    capabilities.value = null
    error.value = errorMessage(e, 'Could not load database capabilities.')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <Teleport defer to="#settings-header-actions">
    <Tooltip text="Refresh database capabilities">
      <Button
        variant="ghost"
        icon="lucide-refresh-cw"
        :loading="loading"
        aria-label="Refresh database capabilities"
        @click="load"
      />
    </Tooltip>
  </Teleport>

  <div v-if="loading && !capabilities" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="flex flex-col gap-6">
    <ErrorMessage v-if="error" :message="error" />

    <Alert
      v-if="showManagedMariaDBDisclaimer"
      title="Database actions are available only for Pilot-managed MariaDB instances."
      :dismissible="false"
    />

    <template v-if="capabilities">
      <p v-if="insight" class="-mt-2 text-ink-gray-6 text-p-base">{{ insight }}</p>

      <div class="gap-4 grid sm:grid-cols-2">
        <section
          v-if="memoryBreakdown"
          class="flex sm:flex-row flex-col sm:items-center gap-6 sm:col-span-2 bg-surface-gray-1 p-4 rounded-6"
        >
          <MemoryRing :percent="memoryRing.percent" />

          <div class="flex flex-col flex-1 gap-1 w-full min-w-0">
            <header class="flex justify-between items-center gap-2 h-5">
              <h4 class="text-ink-gray-8 text-sm">Database memory</h4>

              <Button
                v-if="memoryShare?.available"
                variant="ghost"
                size="sm"
                class="-my-1"
                icon="lucide-bolt"
                aria-label="Change memory split"
                :disabled="Boolean(activeAction)"
                @click="editStat(openMemorySplit)"
              />
            </header>

            <div class="flex justify-between items-center gap-4 h-7 text-base">
              <span class="text-ink-gray-6">Apps, workers and Redis</span>
              <span class="tabular-nums text-ink-gray-8">{{ memoryRing.apps }}</span>
            </div>

            <div class="flex justify-between items-center gap-4 h-7 text-base">
              <span class="text-ink-gray-6">InnoDB buffer pool</span>

              <span class="tabular-nums text-ink-gray-8">
                {{ memoryRing.bufferPool ? formatGB(memoryRing.bufferPool) : '-' }}
              </span>
            </div>
          </div>
        </section>

        <section
          v-for="stat in stats"
          :key="stat.key"
          class="flex flex-col gap-3 border border-outline-gray-1.5 p-4 rounded-6"
        >
          <header class="flex justify-between items-center gap-2 h-5 text-sm">
            <span
              v-if="sizingAction?.action === stat.key"
              class="truncate"
              :class="editNote.class"
              :title="editNote.text"
            >
              {{ editNote.text }}
            </span>

            <h4 v-else class="text-ink-gray-8 truncate">{{ stat.label }}</h4>

            <Button
              v-if="stat.available && sizingAction?.action !== stat.key"
              variant="ghost"
              size="sm"
              class="-my-1"
              icon="lucide-bolt"
              :aria-label="`Edit ${stat.label}`"
              :disabled="Boolean(activeAction)"
              @click="editStat(stat.edit)"
            />
          </header>

          <div v-if="sizingAction?.action === stat.key" class="flex items-center gap-1.5 h-7">
            <TextInput
              v-model.number="sizingValue"
              type="number"
              size="sm"
              :class="['flex-1 min-w-20', sizingAction.unit ? '[&_input]:!pe-20' : '[&_input]:!pe-14']"
              :min="sizingAction.min"
              :max="sizingAction.max"
              step="1"
              autocomplete="off"
              autofocus
              :aria-label="sizingAction.inputLabel"
              @keydown.enter="runSizingAction"
              @keydown.esc="cancelSizing"
            >
              <template v-if="sizingAction.max !== null" #suffix>
                <span class="text-ink-gray-5 text-xs whitespace-nowrap">
                  of {{ sizingAction.max }} {{ sizingAction.unit }}
                </span>
              </template>
            </TextInput>

            <Button
              variant="ghost"
              size="sm"
              icon="lucide-x"
              aria-label="Cancel"
              :disabled="Boolean(activeAction)"
              @click="cancelSizing"
            />

            <Button
              size="sm"
              icon="lucide-check"
              :aria-label="sizingRequiresRestart ? 'Update and restart' : 'Update'"
              :loading="Boolean(activeAction)"
              :disabled="Boolean(sizingValidationError) || sizingUnchanged"
              @click="runSizingAction"
            />
          </div>

          <span v-else class="flex justify-between items-baseline gap-2 h-7 leading-7 tabular-nums">
            <span class="flex items-baseline gap-1">
              <span class="text-xl-semibold text-ink-gray-9">{{ stat.amount }}</span>
              <span v-if="stat.unit" class="text-ink-gray-5 text-sm">{{ stat.unit }}</span>
            </span>

            <span class="text-ink-gray-5 text-sm truncate">{{ stat.note }}</span>
          </span>

          <Progress
            v-if="stat.percent !== null"
            :value="stat.percent"
            size="sm"
            class="mt-auto"
            :class="{ invisible: sizingAction?.action === stat.key }"
          />
        </section>
      </div>

      <div class="flex flex-col divide-y divide-outline-alpha-gray-1">
        <div class="flex justify-between items-center gap-3 py-3">
          <div class="flex flex-col gap-1 min-w-0">
            <span class="text-base text-ink-gray-8">Performance Schema</span>

            <span class="text-ink-gray-6 text-p-sm">
              Collect database instrumentation for deeper performance diagnostics.
            </span>
          </div>

          <Switch
            :model-value="Boolean(action('performance_schema').enabled)"
            :disabled="!action('performance_schema').available || Boolean(activeAction)"
            aria-label="Performance Schema"
            @update:model-value="confirmPerformanceSchema"
          />
        </div>

        <div class="flex justify-between items-center gap-3 py-3">
          <div class="flex flex-col gap-1 min-w-0">
            <span class="text-base text-ink-gray-8">Binary logs</span>
            <span class="text-ink-gray-6 text-p-sm">Retention, size limit and purging.</span>
          </div>

          <Button
            label="Manage"
            :disabled="!action('manage_binlogs').available"
            @click="binlogsOpen = true"
          />
        </div>

        <div class="flex justify-between items-center gap-3 py-3">
          <div class="flex flex-col gap-1 min-w-0">
            <span class="text-base text-ink-gray-8">MariaDB service</span>

            <span class="text-ink-gray-6 text-p-sm">
              Restart the database service and verify that it accepts connections.
            </span>
          </div>

          <Button
            label="Restart"
            :disabled="!action('restart').available || Boolean(activeAction)"
            :loading="activeAction === 'restart'"
            @click="confirmRestart"
          />
        </div>

        <slot />
      </div>
    </template>
  </div>

  <BinlogSettingsDialog v-model="binlogsOpen" />

  <Dialog
    v-model="memorySplitOpen"
    title="Memory split"
    :message="
      memoryShare?.total_memory_mb
        ? `Choose how much of the server's ${formatGB(memoryShare.total_memory_mb)} the database may use.`
        : undefined
    "
    size="md"
  >
    <div v-if="memoryShare?.total_memory_mb" class="flex flex-col gap-4">
      <div class="flex items-center gap-5 bg-surface-gray-1 p-4 rounded-6">
        <MemoryRing :percent="memorySplitPercent" />

        <div class="flex flex-col flex-1 gap-3 min-w-0">
          <div class="flex flex-col divide-y divide-outline-gray-1">
            <div
              v-for="(side, index) in memorySplitSides"
              :key="side.label"
              class="flex justify-between items-center gap-4 h-8 text-base"
            >
              <span class="flex items-center gap-2 min-w-0 text-ink-gray-6">
                <span
                  class="bg-current rounded-full size-2 shrink-0"
                  :class="index ? 'text-ink-gray-3' : 'text-ink-gray-8'"
                />

                <span class="truncate">{{ side.label }}</span>
              </span>

              <span class="tabular-nums text-ink-gray-8">{{ side.size }}</span>
            </div>
          </div>

          <Slider
            v-model="memorySplitSlider"
            :min="sharePercent(memoryShare.min ?? 0.2)"
            :max="sharePercent(memoryShare.max ?? 0.8)"
            :step="5"
            aria-label="Database share of server memory"
          />
        </div>
      </div>

      <div v-if="memorySplitStep" class="flex flex-col">
        <div class="flex justify-between items-center gap-4 h-7 text-base">
          <span class="text-ink-gray-5">InnoDB buffer pool</span>

          <span class="tabular-nums text-ink-gray-8">
            {{ formatGB(memorySplitStep.innodb_buffer_pool_mb) }}
          </span>
        </div>

        <div class="flex justify-between items-center gap-4 h-7 text-base">
          <span class="text-ink-gray-5">Memory limit</span>

          <span class="tabular-nums text-ink-gray-8">
            {{ formatGB(memorySplitStep.memory_max_mb) }}
          </span>
        </div>
      </div>

      <Alert title="A larger buffer pool may need a restart to apply." :dismissible="false" />

      <ErrorMessage v-if="actionError" :message="actionError" />
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button
          variant="ghost"
          label="Cancel"
          :disabled="Boolean(activeAction)"
          @click="memorySplitOpen = false"
        />

        <Button
          variant="solid"
          label="Apply"
          :loading="activeAction === 'memory_share'"
          :disabled="sharePercent(memoryShare?.current ?? 0) === memorySplitPercent"
          @click="applyMemorySplit"
        />
      </div>
    </template>
  </Dialog>

  <Dialog v-model="confirmationOpen" :title="confirmationTitle" size="sm">
    <p v-if="confirmation?.message" class="text-ink-gray-7 text-p-sm">
      {{ confirmation.message }}
    </p>

    <ErrorMessage v-if="actionError" :message="actionError" class="mt-3" />

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" :disabled="Boolean(activeAction)" @click="confirmationOpen = false">
          Cancel
        </Button>

        <Button variant="solid" :loading="Boolean(activeAction)" @click="runConfirmedAction">
          {{ confirmation?.buttonLabel }}
        </Button>
      </div>
    </template>
  </Dialog>
</template>
