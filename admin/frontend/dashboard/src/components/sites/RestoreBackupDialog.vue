<script setup lang="ts">
import { Checkbox, Select } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useSites } from '@/composables/sites/useSites'
import type { Backup } from '@/types/siteBackups'
import { errorMessage } from '@/utils/error'
import { fmtDateTime } from '@/utils/taskFormat'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
  backup: Backup | null
}

const props = defineProps<Props>()
const open = defineModel<boolean>('open', { default: false })
const router = useRouter()
const { names: siteNames, load: loadSites } = useSites()

const PARTS = [
  { part: 'database', kind: 'database', label: 'Database' },
  { part: 'public', kind: 'public-file', label: 'Public files' },
  { part: 'private', kind: 'private-file', label: 'Private files' },
]

const target = ref('')
const chosen = ref<Record<string, boolean>>({})
const restoring = ref(false)
const error = ref('')

const available = computed(() =>
  PARTS.filter(({ kind }) => props.backup?.files.some((file) => file.kind === kind)),
)
const parts = computed(() =>
  available.value.filter(({ part }) => chosen.value[part]).map(({ part }) => part),
)
const siteOptions = computed(() => siteNames.value.map((name) => ({ label: name, value: name })))

watch(open, (isOpen) => {
  if (!isOpen) return
  target.value = props.siteName
  chosen.value = Object.fromEntries(available.value.map(({ part }) => [part, true]))
  error.value = ''
  loadSites()
})

const confirm = async () => {
  if (!props.backup) return
  restoring.value = true
  error.value = ''
  try {
    const data = await sitesApi.restore(target.value, {
      parts: parts.value,
      source_site: props.siteName,
      backup_timestamp: props.backup.timestamp,
    })
    if (data.task_id) {
      open.value = false
      openTaskDetailPage(router, data.task_id)
    } else error.value = apiErrorMessage(data, 'Could not start the restore.')
  } catch (e) {
    error.value = errorMessage(e, 'Could not start the restore.')
  } finally {
    restoring.value = false
  }
}
</script>

<template>
  <ActionDialog
    v-model:open="open"
    title="Restore backup"
    :error="error"
    confirm-label="Restore"
    confirm-theme="red"
    :loading="restoring"
    :disabled="!target || !parts.length"
    @confirm="confirm"
  >
    <p class="text-ink-gray-6 text-p-sm">
      Backup from {{ backup ? fmtDateTime(backup.created_at) : '' }}
    </p>
    <div>
      <Select v-model="target" label="Restore into" :options="siteOptions" />
      <div class="flex flex-wrap gap-1 mt-3">
        <Checkbox
          v-for="item in available"
          :key="item.part"
          v-model="chosen[item.part]"
          :label="item.label"
          padded
        />
      </div>
    </div>
  </ActionDialog>
</template>
