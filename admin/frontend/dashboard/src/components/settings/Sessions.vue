<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Button, Dialog, Dropdown, Spinner, Tooltip, toast } from 'frappe-ui'

import Table from '@/components/common/Table.vue'

import EmptyState from '@/components/common/EmptyState.vue'

import { auditApi } from '@/api/audit'
import { sessionApi } from '@/api/session'
import { relativeTime } from '@/utils/time'
import { commandLabel, fmtDateTime } from '@/utils/taskFormat'

const nestedView = defineModel('nestedView')
// The session whose activity is showing - a route param (owned by SettingsDialog),
// so a specific session's activity view is a real, deep-linkable URL.
const jti = defineModel('jti')

const loading = ref(true)
const loadError = ref('')
const activeTokens = ref([])
const currentJti = ref('')
const showRevoke = ref(false)
const revoking = ref(null)
const revokeBusy = ref(false)

const activityLoading = ref(false)
const activity = ref([])

// Titled by jti, not IP - the same IP can hold multiple sessions, so IP alone
// wouldn't identify which one this view is showing.
watch(
  jti,
  (currentTarget) => {
    nestedView.value = currentTarget ? { title: `Session - ${currentTarget}` } : null
  },
  { immediate: true },
)

watch(
  jti,
  async (target) => {
    if (!target) {
      activity.value = []
      return
    }
    activityLoading.value = true
    try {
      const result = await auditApi.list({ jti: target, limit: 50 })
      activity.value = result.data || []
    } catch (e) {
      toast.error(e.message || 'Could not load activity.')
    } finally {
      activityLoading.value = false
    }
  },
  { immediate: true },
)

const columns = [
  { label: 'IP address', key: 'ip' },
  { label: 'Last activity', key: 'activity' },
  { label: 'Expires', key: 'exp' },
  { label: '', key: 'actions', class: 'w-12' },
]

const activityColumns = [
  { label: 'Event', key: 'event', class: 'w-2/5' },
  { label: 'IP address', key: 'ip' },
  { label: 'Time', key: 'time' },
  { label: '', key: 'actions', class: 'w-12' },
]

// All display formatting happens here, not in the template - rows already hold the
// exact strings/tooltips each column renders.
const rows = computed(() =>
  activeTokens.value
    .map((t) => {
      const isCurrent = t.jti === currentJti.value
      const lastSeenAgo = t.last_seen && relativeTime(t.last_seen * 1000)
      return {
        jti: t.jti,
        ip: t.ip || '-',
        isCurrent,
        lastSeen: t.last_seen || 0,
        activity: isCurrent ? 'Current session' : lastSeenAgo || '-',
        activityTooltip: isCurrent ? '' : formatDate(t.last_seen),
        exp: formatDate(t.exp),
      }
    })
    .sort((a, b) => b.isCurrent - a.isCurrent || b.lastSeen - a.lastSeen),
)

const activityRows = computed(() =>
  activity.value.map((entry, i) => ({
    key: i,
    event: auditEntryHead(entry),
    eventTooltip: auditEntryDetail(entry),
    ip: entry.ip || '-',
    time: relativeTime(entry.logged_at),
    timeExact: fmtDateTime(entry.logged_at),
    raw: entry,
  })),
)

const showDetail = ref(false)
const viewingDetail = ref(null)

const openDetail = (row) => {
  viewingDetail.value = row.raw
  showDetail.value = true
}

// Flattens args into the top level and drops empties, so the dialog is a plain,
// complete key/value dump of the raw audit record - no per-type formatting to maintain.
const detailEntries = computed(() => {
  if (!viewingDetail.value) return []
  const { args, ...rest } = viewingDetail.value
  return Object.entries({ ...rest, ...args })
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => ({
      key,
      value: typeof value === 'object' ? JSON.stringify(value) : String(value),
    }))
})

const AUDIT_TYPE_LABELS = { ssh_key: 'SSH Key' }

const auditTypeLabel = (type) => {
  if (!type) return ''
  return AUDIT_TYPE_LABELS[type] || type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

// A "task" entry's real name is its command (e.g. backup-site); everything else is
// already a distinct audit type (session, backup, ssh_key, git...). Whatever
// identifying detail the entry carries (queued tasks nest theirs under `args`, other
// audit types put them at the top level) is appended so the row is self-contained.
const DETAIL_KEYS = [
  'site',
  'app',
  'name',
  'repo',
  'branch',
  'marketplace_app',
  'timestamp',
  'file',
  'fingerprint',
  'status',
]

// Short enough to fit the column without forcing the row wider than its container -
// full context (below) only shows up in the hover tooltip.
const auditEntryHead = (entry) => {
  const label = entry.type === 'task' ? commandLabel(entry.command) : auditTypeLabel(entry.type)
  return entry.event ? `${label} ${entry.event}` : label
}

const auditEntryDetail = (entry) => {
  const source = { ...entry, ...entry.args }
  const detail = [...new Set(DETAIL_KEYS.map((key) => source[key]).filter(Boolean))].join(' · ')
  const head = auditEntryHead(entry)
  return detail ? `${head} — ${detail}` : head
}

const formatDate = (seconds) => (seconds ? fmtDateTime(seconds * 1000) : '-')

const menuOptions = (row) => {
  return [
    { label: 'View activity', icon: 'lucide-history', onClick: () => (jti.value = row.jti) },
    { label: 'Revoke session', icon: 'lucide-log-out', theme: 'red', onClick: () => promptRevoke(row) },
  ]
}

const promptRevoke = (row) => {
  revoking.value = row
  showRevoke.value = true
}

const confirmRevoke = async () => {
  revokeBusy.value = true
  try {
    const response = await sessionApi.revoke(revoking.value.jti)
    if (response.ok) {
      toast.success('Session revoked')
      showRevoke.value = false
      await load()
    } else {
      toast.error('Could not revoke session')
    }
  } catch (e) {
    toast.error(e.message || 'Could not revoke session')
  } finally {
    revokeBusy.value = false
  }
}

const load = async () => {
  try {
    const data = await sessionApi.list()
    activeTokens.value = data.active_tokens || []
    currentJti.value = data.current_jti || ''
  } catch (e) {
    loadError.value = e.message || 'Could not load authentication data.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else-if="jti">
    <div v-if="activityLoading" class="flex justify-center items-center h-40">
      <Spinner size="lg" class="text-ink-gray-4" />
    </div>

    <EmptyState
      compact
      v-else-if="!activity.length"
      icon="lucide-history"
      title="No activity recorded"
      description="Actions taken by this session will show up here."
    />
    <Table v-else :columns="activityColumns" :rows="activityRows" height="max-h-96">
      <template #event="{ row }">
        <Tooltip :text="row.eventTooltip">
          <span class="block truncate">{{ row.event }}</span>
        </Tooltip>
      </template>

      <template #time="{ row }">
        <Tooltip :text="row.timeExact">
          <span class="block truncate">{{ row.time }}</span>
        </Tooltip>
      </template>

      <template #actions="{ row }">
        <Button
          variant="ghost"
          icon="lucide-info"
          label="Activity details"
          tooltip="Details"
          @click="openDetail(row)"
        />
      </template>
    </Table>
  </div>

  <div v-else class="space-y-5">
    <div
      v-if="loadError"
      class="py-12 border border-dashed rounded-7 border-outline-red-2 text-ink-red-2 text-p-sm text-center"
    >
      {{ loadError }}
    </div>

    <template v-else>
      <EmptyState
        compact
        v-if="!activeTokens.length"
        icon="lucide-key-round"
        title="No active sessions"
        description="Sign-ins appear here while their tokens are valid."
      />

      <Table v-else :columns="columns" :rows="rows" height="max-h-96">
        <template #activity="{ row }">
          <Tooltip :text="row.activityTooltip">
            <span class="block truncate">{{ row.activity }}</span>
          </Tooltip>
        </template>

        <template #actions="{ row }">
          <Dropdown :options="menuOptions(row)">
            <template #default="{ open }">
              <Button
                variant="ghost"
                :active="open"
                icon="lucide-ellipsis"
                label="Session actions"
                tooltip="Actions"
              />
            </template>
          </Dropdown>
        </template>
      </Table>
    </template>
  </div>

  <Dialog v-model="showRevoke" title="Revoke session" size="md">
    <p class="text-ink-gray-7 text-p-base">
      Revoke this session? Its token stops working immediately and whoever holds it must sign in
      again.
    </p>

    <p class="mt-2 font-mono text-ink-gray-5 text-sm">{{ revoking?.ip }}</p>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showRevoke = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="revokeBusy" @click="confirmRevoke">
          Revoke
        </Button>
      </div>
    </template>
  </Dialog>

  <Dialog v-model="showDetail" title="Activity details" size="md">
    <div class="space-y-2 max-h-96 overflow-y-auto">
      <div v-for="d in detailEntries" :key="d.key" class="flex gap-3 text-p-sm">
        <span class="w-28 shrink-0 text-ink-gray-5 capitalize">{{ d.key.replace(/_/g, ' ') }}</span>
        <span class="text-ink-gray-8 break-all">{{ d.value }}</span>
      </div>
    </div>
  </Dialog>
</template>
