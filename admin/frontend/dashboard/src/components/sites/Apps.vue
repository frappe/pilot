<script setup lang="ts">
import { Button, Dropdown, type DropdownItem, Skeleton, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppIcon from '@/components/apps/AppIcon.vue'
import SwitchBranchDialog from '@/components/apps/SwitchBranchDialog.vue'
import UninstallAppDialog from '@/components/apps/UninstallAppDialog.vue'
import Table from '@/components/common/Table.vue'

import { appsApi } from '@/api/apps'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useSession } from '@/composables/auth/useSession'
import { useSite } from '@/composables/sites/useSite'
import type { SiteHistory } from '@/types/siteHistory'
import { errorMessage } from '@/utils/error'
import { toSentenceCase } from '@/utils/format'
import { openTaskDetailPage } from '@/utils/taskRoute'

interface Props {
  siteName: string
}

interface AppCardEntry {
  name: string
  title: string
  label: string
  logo_url: string | null
  documentation: string
  website: string
  badge: string
  missing: boolean
  unfinished: boolean
}

const props = defineProps<Props>()
const { session } = useSession()
const router = useRouter()

const { apps, canDisableApps, installedApps, appsLoading, loadApps, reload } = useSite(
  props.siteName,
)
const { titleMap, logoMap, documentationMap, websiteMap, load: loadRegistry } = useAppRegistry()

// The list renders `installedApps`, which rides on the site payload - refreshing the
// app metadata alone would leave a just-disabled app on screen.
const refresh = async () => {
  await Promise.all([reload(), loadApps()])
}

const appDetailMap = computed(() => Object.fromEntries(apps.value.map((a) => [a.name, a])))

const history = ref<SiteHistory | null>(null)
const dateFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

const installedOn = computed(() =>
  Object.fromEntries(
    (history.value?.apps ?? []).map((app) => [
      app.app,
      dateFormat.format(new Date(app.installed_at.replace(' ', 'T'))),
    ]),
  ),
)

const columns = [
  { key: 'app', label: 'App', class: 'w-[36%]' },
  { key: 'version', label: 'Version', class: 'w-[14%]' },
  { key: 'branch', label: 'Branch', class: 'w-[20%]' },
  { key: 'commit', label: 'Commit', class: 'w-[14%]' },
  { key: 'installed', label: 'Installed', class: 'w-[14%]' },
  { key: 'actions', label: '', class: 'text-right' },
]

// Disabling needs a Frappe that supports it, and an app the catalog can reinstall.
const canDisable = (app: AppCardEntry | null) => {
  return canDisableApps.value && Boolean(app && appDetailMap.value[app.name]?.is_marketplace)
}

// Registry metadata describes the listed repository, not a fork with the same name.
const registryValue = (map: Record<string, string>, name: string) =>
  appDetailMap.value[name]?.is_marketplace ? map[name] : ''

const appObjects = computed<AppCardEntry[]>(() =>
  installedApps.value.map((name) => {
    const detail = appDetailMap.value[name]
    return {
      name,
      title: registryValue(titleMap.value, name) || toSentenceCase(detail?.title) || name,
      label: detail?.missing
        ? 'Not on bench'
        : detail?.install_unfinished
          ? 'Install incomplete'
          : (detail?.follow_branch && detail.branch) || detail?.version || '',
      badge: detail?.follow_branch ? 'Latest commits' : '',
      logo_url: registryValue(logoMap.value, name) || null,
      documentation: registryValue(documentationMap.value, name),
      website: registryValue(websiteMap.value, name),
      missing: Boolean(detail?.missing),
      unfinished: Boolean(detail?.install_unfinished),
    }
  }),
)

const showUninstall = ref(false)
const uninstallTarget = ref<AppCardEntry | null>(null)

const showSwitchBranch = ref(false)
const switchBranchApp = ref<AppCardEntry | null>(null)
const switchBranchTarget = computed(() => {
  const app = switchBranchApp.value
  const detail = app && appDetailMap.value[app.name]
  return app && detail ? { ...app, branch: detail.branch, repo: detail.repo } : null
})

const openLink = (url: string) => {
  window.open(url, '_blank', 'noopener,noreferrer')
}

const retryInstall = async (app: AppCardEntry) => {
  try {
    const result = await sitesApi.apps.install(props.siteName, { app: app.name })
    if ('task_id' in result && result.task_id) openTaskDetailPage(router, result.task_id)
    else toast.error(apiErrorMessage(result, `Could not retry ${app.title}.`))
  } catch (caught) {
    toast.error(errorMessage(caught, `Could not retry ${app.title}.`))
  }
}

const setFollowBranch = async (app: AppCardEntry, followBranch: boolean) => {
  const fallback = `Could not change where ${app.title} gets updates from.`
  try {
    const result = await appsApi.followBranch(app.name, followBranch)
    const source = followBranch
      ? `the latest commits on ${appDetailMap.value[app.name]?.branch || 'its branch'}`
      : 'marketplace releases'
    if (result.task_id)
      toast.success(`${app.title} now updates to ${source}. Checking for updates…`)
    else toast.error(apiErrorMessage(result, fallback))
  } catch (caught) {
    toast.error(errorMessage(caught, fallback))
  }
  await loadApps()
}

const menuOptions = (app: AppCardEntry): DropdownItem[] => {
  const options: DropdownItem[] = []

  if (app.website)
    options.push({ label: 'Website', icon: 'lucide-globe', onClick: () => openLink(app.website) })

  if (app.documentation)
    options.push({
      label: 'Documentation',
      icon: 'lucide-book-open',
      onClick: () => openLink(app.documentation),
    })

  if (app.unfinished)
    options.push({
      label: 'Retry install',
      icon: 'lucide-rotate-ccw',
      onClick: () => retryInstall(app),
    })

  if (session.codeEditor)
    options.push({
      label: 'Open in editor',
      icon: 'lucide-code',
      onClick: () => openLink(`/editor/${encodeURIComponent(app.name)}`),
    })

  // Branches are listed through the GitHub provider.
  if (appDetailMap.value[app.name]?.repo?.includes('github.com'))
    options.push({
      label: 'Switch branch',
      icon: 'lucide-git-branch',
      onClick: () => {
        switchBranchApp.value = app
        showSwitchBranch.value = true
      },
    })

  const detail = appDetailMap.value[app.name]
  if (detail?.is_marketplace)
    options.push(
      detail.follow_branch
        ? {
            label: 'Use marketplace releases',
            icon: 'lucide-package',
            onClick: () => setFollowBranch(app, false),
          }
        : {
            label: 'Use latest commits',
            icon: 'lucide-git-commit-horizontal',
            onClick: () => setFollowBranch(app, true),
          },
    )

  if (app.name !== 'frappe')
    options.push({
      label: canDisable(app) ? 'Remove' : 'Uninstall',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () => {
        uninstallTarget.value = app
        showUninstall.value = true
      },
    })

  return options
}

onMounted(() => {
  loadApps()
  loadRegistry()
  sitesApi
    .history(props.siteName)
    .then((data) => (history.value = data))
    .catch(() => null)
})
</script>

<template>
  <div v-if="appsLoading && !installedApps.length" class="flex flex-col gap-2">
    <Skeleton v-for="i in 4" :key="i" class="rounded-4 h-14" />
  </div>

  <p v-else-if="!installedApps.length" class="py-12 text-ink-gray-5 text-sm text-center">
    No apps installed on this site.
  </p>

  <Table v-else :columns="columns" :rows="appObjects" :row-height="36">
    <template #app="{ row }">
      <span class="flex items-center gap-2.5 min-w-0">
        <AppIcon :name="row.name" :label="row.title" :logo="row.logo_url || ''" size="xs" />
        <span class="text-ink-gray-8 truncate">{{ row.title }}</span>
      </span>
    </template>

    <template #version="{ row }">
      <span :class="row.missing || row.unfinished ? 'text-ink-amber-6' : 'text-ink-gray-7'">
        {{ row.missing || row.unfinished ? row.label : appDetailMap[row.name]?.version || '-' }}
      </span>
    </template>

    <template #branch="{ row }">
      <span class="text-ink-gray-7">{{ appDetailMap[row.name]?.branch || '-' }}</span>
    </template>

    <template #commit="{ row }">
      <span class="font-mono text-ink-gray-6 text-xs">
        {{ appDetailMap[row.name]?.commit?.slice(0, 7) || '-' }}
      </span>
    </template>

    <template #installed="{ row }">
      <span class="tabular-nums text-ink-gray-7">{{ installedOn[row.name] || '-' }}</span>
    </template>

    <template #actions="{ row }">
      <Dropdown v-if="menuOptions(row).length" :options="menuOptions(row)">
        <template #default="{ open }">
          <Button
            variant="ghost"
            :active="open"
            icon="lucide-ellipsis"
            label="App actions"
            tooltip="Actions"
          />
        </template>
      </Dropdown>
    </template>
  </Table>

  <SwitchBranchDialog v-model:open="showSwitchBranch" :app="switchBranchTarget" />

  <UninstallAppDialog
    v-model:open="showUninstall"
    :app="uninstallTarget"
    :site-name="siteName"
    :can-disable="canDisable(uninstallTarget)"
    @disabled="refresh"
  />
</template>
