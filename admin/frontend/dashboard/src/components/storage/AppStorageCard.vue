<script setup lang="ts">
import { Tree } from 'frappe-ui'
import { computed, onMounted, reactive, ref } from 'vue'

import AppIcon from '@/components/apps/AppIcon.vue'
import UsageMeter from '@/components/common/UsageMeter.vue'

import { appsApi } from '@/api/apps'
import type { BenchBreakdown, SiteStorage } from '@/types/storage'
import { formatBytes } from '@/utils/format'

interface Props {
  data: BenchBreakdown
  diskTotal: number
}

interface StorageNode {
  key: string
  label: string
  bytes: number
  dot?: string
  logo?: string
  icon?: string
  muted?: boolean
  children?: StorageNode[]
  expanded?: boolean
}

const asStorageNode = (node: unknown) => node as StorageNode

const props = defineProps<Props>()

const logoByName = ref<Record<string, string>>({})

onMounted(async () => {
  try {
    const registry = await appsApi.marketplace()
    logoByName.value = Object.fromEntries(
      registry.filter((app) => app.logo_url).map((app) => [app.name, app.logo_url]),
    )
  } catch {
    logoByName.value = {}
  }
})

const COLORS = { apps: 'blue-7', sites: 'violet-7', logs: 'amber-7' }

const bySize = <T extends { bytes: number }>(items: T[]) => [...items].sort((a, b) => b.bytes - a.bytes)

const siteSubItems = (site: SiteStorage) =>
  bySize([
    { label: 'Private files', bytes: site.private_files_bytes, entries: [] as { name: string; bytes: number }[] },
    { label: 'Public files', bytes: site.public_files_bytes, entries: [] as { name: string; bytes: number }[] },
    { label: 'Backups', bytes: site.backups_bytes, entries: bySize(site.backup_files) },
    { label: 'Other', bytes: site.other_bytes, entries: bySize(site.other_entries) },
  ])

const barParts = computed(() => [
  { label: 'Apps', bytes: props.data.apps_bytes, color: COLORS.apps },
  { label: 'Site files', bytes: props.data.sites_bytes, color: COLORS.sites },
  { label: 'Logs', bytes: props.data.logs_bytes, color: COLORS.logs },
])

const treeNodes = computed(() => reactive([
  {
    key: 'apps',
    label: 'Apps',
    bytes: props.data.apps_bytes,
    dot: `var(--ink-${COLORS.apps})`,
    expanded: false,
    children: bySize(props.data.apps).map((app) => ({
      key: `app:${app.name}`,
      label: app.name,
      bytes: app.bytes,
      logo: logoByName.value[app.name],
    })),
  },
  {
    key: 'sites',
    label: 'Site files',
    bytes: props.data.sites_bytes,
    dot: `var(--ink-${COLORS.sites})`,
    expanded: false,
    children: bySize(props.data.sites).map((site) => ({
      key: `site:${site.name}`,
      label: site.name,
      bytes: site.bytes,
      icon: 'lucide-globe',
      expanded: false,
      children: siteSubItems(site).map((sub) => ({
        key: `site:${site.name}:${sub.label}`,
        label: sub.label,
        bytes: sub.bytes,
        muted: true,
        expanded: false,
        children: sub.entries.length
          ? sub.entries.map((entry) => ({
              key: `site:${site.name}:${sub.label}:${entry.name}`,
              label: entry.name,
              bytes: entry.bytes,
              muted: true,
            }))
          : undefined,
      })),
    })),
  },
  {
    key: 'logs',
    label: 'Logs',
    bytes: props.data.logs_bytes,
    dot: `var(--ink-${COLORS.logs})`,
  },
]))
</script>

<template>
  <section class="p-5 min-w-0">
    <div class="flex justify-between items-center gap-3 mb-4">
      <h3 class="flex items-center gap-2 font-medium text-ink-gray-8">
        <span class="size-4 lucide-box" />
        App storage
      </h3>

      <div class="text-ink-gray-6 text-sm">
        <span class="font-medium text-ink-gray-8">{{ formatBytes(data.used_bytes) }}</span>
        of {{ formatBytes(diskTotal) }} used
      </div>
    </div>

    <UsageMeter :parts="barParts" :total="diskTotal" :legend="false" bar-height="h-5" />

    <Tree
      :nodes="treeNodes"
      node-key="key"
      guides="connectors"
      class="mt-3"
      style="--tree-row-height: 28px"
    >
      <template #item="{ node: rawNode, hasChildren, expanded }">
        <span
          v-if="asStorageNode(rawNode).dot"
          class="rounded-full size-2 shrink-0"
          :style="{ backgroundColor: asStorageNode(rawNode).dot }"
        />
        <AppIcon
          v-else-if="'logo' in rawNode"
          :name="asStorageNode(rawNode).label"
          :logo="asStorageNode(rawNode).logo || ''"
          size="xs"
        />
        <span
          v-else-if="asStorageNode(rawNode).icon"
          class="size-3.5 text-ink-gray-4 shrink-0"
          :class="asStorageNode(rawNode).icon"
        />

        <span
          class="text-sm truncate"
          :class="asStorageNode(rawNode).muted ? 'text-ink-gray-5' : 'text-ink-gray-7'"
        >
          {{ asStorageNode(rawNode).label }}
        </span>

        <span v-if="hasChildren"
          class="transition-all size-3 text-ink-gray-5 shrink-0 lucide-chevron-up"
          :class="{ 'rotate-180': !expanded }" />

        <span
          class="ml-auto text-sm tabular-nums shrink-0"
          :class="asStorageNode(rawNode).muted ? 'text-ink-gray-6' : 'text-ink-gray-8'"
        >
          {{ formatBytes(asStorageNode(rawNode).bytes) }}
        </span>
      </template>
    </Tree>
  </section>
</template>
