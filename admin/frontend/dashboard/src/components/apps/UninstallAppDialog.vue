<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import ActionDialog from '@/components/common/ActionDialog.vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  app?: Record<string, any> | null
  siteName: string
  // Only marketplace apps can be disabled - a disabled app is re-enabled by
  // installing it again, which needs the app to still be in the catalog.
  canDisable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  app: null,
  canDisable: false,
})
const open = defineModel('open')
const emit = defineEmits(['disabled'])
const router = useRouter()

const mode = ref('uninstall')
const working = ref(false)
const error = ref('')

const appLabel = computed(() => props.app?.title || props.app?.name || '')

const uninstallWarning = computed(() => ({
  title: `This can't be undone.`,
  message: `Every doctype ${appLabel.value} owns is dropped from ${props.siteName}, along with the records in it. Back the site up first if you need the data.`,
}))

const options = computed(() => [
  {
    value: 'disable',
    label: 'Disable',
    icon: 'lucide-circle-slash',
    description: `${appLabel.value} stops working on the site. Its doctypes and records stay, so installing it again brings the data back.`,
  },
  {
    value: 'uninstall',
    label: 'Uninstall',
    icon: 'lucide-trash-2',
    description: `${appLabel.value} stops working, and every doctype it owns is dropped with its records. This can't be undone.`,
  },
])

watch(open, (isOpen) => {
  if (!isOpen) return
  error.value = ''
  mode.value = props.canDisable ? 'disable' : 'uninstall'
})

const confirmRemoval = async () => {
  if (!props.app || working.value) return
  error.value = ''
  working.value = true
  try {
    if (mode.value === 'disable') await disableApp()
    else await uninstallApp()
  } catch (caught) {
    error.value = caught.message || 'Could not start removal.'
  } finally {
    working.value = false
  }
}

const disableApp = async () => {
  const result = await sitesApi.apps.remove(props.siteName, props.app.name, { mode: 'disable' })
  if (!result.disabled) throw new Error(apiErrorMessage(result, 'Could not disable app.'))
  open.value = false
  emit('disabled', props.app.name)
}

const uninstallApp = async () => {
  const result = await sitesApi.apps.remove(props.siteName, props.app.name)
  if (!result.task_id) throw new Error(apiErrorMessage(result, 'Uninstall failed.'))
  open.value = false
  openTaskDetailPage(router, result.task_id)
}
</script>

<template>
  <ActionDialog
    v-model:open="open"
    :title="canDisable ? 'Remove App' : 'Uninstall App'"
    :subject="{ name: app?.name, label: appLabel, badge: app?.label, description: app?.description, logo: app?.logo_url }"
    :warning="canDisable ? null : uninstallWarning"
    :error="error"
    :confirm-label="mode === 'disable' ? 'Disable' : 'Uninstall'"
    :confirm-theme="mode === 'disable' ? 'gray' : 'red'"
    :loading="working"
    @confirm="confirmRemoval"
  >
    <div v-if="canDisable" class="gap-1.5 grid">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        class="flex items-start gap-2.5 p-2.5 rounded-4 text-left transition duration-150 ease-[var(--ease-out)] active:scale-[0.98]"
        :class="mode === option.value ? 'bg-surface-gray-3' : 'hover:bg-surface-gray-2'"
        @click="mode = option.value"
      >
        <span class="mt-0.5 size-4 text-ink-gray-6 shrink-0" :class="option.icon" />
        <span class="min-w-0">
          <span class="block text-ink-gray-8">{{ option.label }}</span>
          <span class="block mt-0.5 text-ink-gray-5 text-p-sm leading-5">{{ option.description }}</span>
        </span>
      </button>
    </div>
  </ActionDialog>
</template>
