<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { LoadingText } from 'frappe-ui'

import AppIcon from '@/components/apps/AppIcon.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'

import { appsApi } from '@/api/apps'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import type { AppInfo } from '@/types/apps'

const { session } = useSession()
const { titleMap, logoMap, load: loadRegistry } = useAppRegistry()

const installed = ref<AppInfo[]>([])
const loading = ref(true)

const columns = [
  { key: 'app', label: 'App', class: 'w-[22%]' },
  { key: 'branch', label: 'Branch', class: 'w-[18%]' },
  { key: 'commit', label: 'Last commit' },
  { key: 'version', label: 'Version', class: 'w-[10%]' },
  { key: 'status', label: 'Status', class: 'w-[12%]' },
]

const rows = computed(() =>
  installed.value.map((app) => ({
    ...app,
    id: app.name,
    title: titleMap.value[app.name] || app.title || app.name,
    logo: logoMap.value[app.name] || '',
  })),
)

const editorUrl = (name: string) => `/editor/${encodeURIComponent(name)}`

onMounted(async () => {
  loadRegistry()

  try {
    installed.value = await appsApi.installed()
  } catch {
    installed.value = []
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="p-3 md:p-4">
    <EmptyState
      v-if="!session.codeEditor"
      icon="lucide-code"
      title="The code editor is off"
      description="Turn it on in Settings to browse and edit app files."
    />

    <LoadingText v-else-if="loading" class="justify-center mt-16" />

    <EmptyState
      v-else-if="!rows.length"
      icon="lucide-package"
      title="No apps on this bench"
      description="Install an app from the Marketplace to edit its files."
    />

    <Table v-else :columns="columns" :rows="rows" :row-height="44">
      <template #app="{ row }">
        <span class="flex items-center gap-2.5 min-w-0">
          <AppIcon :name="row.name" :label="row.title" :logo="row.logo" size="xs" />

          <a
            :href="editorUrl(row.name)"
            target="_blank"
            rel="noopener"
            class="after:absolute after:inset-0 text-ink-gray-8 truncate"
          >
            {{ row.title }}
          </a>
        </span>
      </template>

      <template #branch="{ row }">
        <span class="flex items-center gap-1.5 min-w-0 text-ink-gray-7">
          <span class="size-3.5 text-ink-gray-4 lucide-git-branch shrink-0" />
          <span class="font-mono text-sm truncate">{{ row.branch || '-' }}</span>
        </span>
      </template>

      <template #commit="{ row }">
        <span class="flex items-baseline gap-2 min-w-0 max-w-[36rem]">
          <span class="text-ink-gray-7 truncate">{{ row.commit_message }}</span>

          <span class="font-mono text-ink-gray-5 text-xs shrink-0">
            {{ row.current_commit.slice(0, 7) }}
          </span>
        </span>
      </template>

      <template #version="{ row }">
        <span class="tabular-nums text-ink-gray-7">{{ row.installed_version || '-' }}</span>
      </template>

      <template #status="{ row }">
        <span v-if="row.has_local_changes" class="flex items-center gap-1.5 text-ink-amber-6">
          <span class="bg-surface-amber-5 rounded-full size-1.5" />
          Uncommitted
        </span>

        <span v-else class="text-ink-gray-5">Clean</span>
      </template>
    </Table>
  </div>
</template>
