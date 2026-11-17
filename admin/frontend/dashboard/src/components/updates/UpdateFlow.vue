<script setup lang="ts">
import { Button, Spinner, Tooltip } from 'frappe-ui'
import { type Component, computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import Collapsable from '@/components/common/Collapsable.vue'
import MigrateIcon from '@/components/icons/MigrateIcon.vue'
import RevertAppsIcon from '@/components/icons/RevertAppsIcon.vue'
import UpdateAppsIcon from '@/components/icons/UpdateAppsIcon.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import type {
  MigrationApp,
  MigrationChainLink,
  MigrationSite,
  MigrationSummary,
} from '@/types/migrations'
import { fmtDuration } from '@/utils/taskFormat'

type Status = 'done' | 'running' | 'failed' | 'stopped' | 'waiting' | 'skipped' | 'unknown'
type Child = { id: string; label: string; status: Status; taskId: string | null; time: string }
type Revision = { from: string; to: string; url: string | null }
type Row = Child & { children: Child[]; revision?: Revision }
type Panel = { key: string; title: string; noun: string; rows: Row[] }
type Stage = {
  key: string
  name: string
  status: Status
  time: string
  panels: Panel[]
  summary: string
}

const props = defineProps<{ op: MigrationSummary }>()

const router = useRouter()
const { titleMap } = useAppRegistry()

const shown = ref(new Set<string>())
const expanded = ref(new Set<string>())

const mark: Record<Status, string> = {
  done: 'lucide-circle-check text-ink-green-6',
  failed: 'lucide-circle-x text-ink-red-6',
  stopped: 'lucide-circle-x text-ink-gray-5',
  running: '',
  waiting: 'lucide-circle-dashed text-ink-gray-4',
  skipped: 'lucide-circle-dashed text-ink-gray-4',
  unknown: 'lucide-circle text-ink-gray-4',
}

const stageIcon: Record<string, Component | string> = {
  backup: 'lucide-database-backup',
  update: UpdateAppsIcon,
  migrate: MigrateIcon,
  restart: 'lucide-power',
  'revert-apps': RevertAppsIcon,
  'restore-backups': 'lucide-archive-restore',
  'restart-services': 'lucide-power',
}

const tileTone: Record<Status, string> = {
  done: 'bg-surface-gray-1 border-outline-gray-1.5 text-ink-gray-6',
  failed: 'bg-surface-red-1 border-outline-red-3 text-ink-red-6',
  stopped: 'bg-surface-gray-1 border-outline-gray-4 text-ink-gray-6',
  running: 'bg-surface-gray-1 border-outline-gray-1.5 text-ink-gray-7',
  waiting: 'border-dashed border-outline-gray-4 text-ink-gray-5',
  skipped: 'border-dashed border-outline-gray-4 text-ink-gray-5',
  unknown: 'bg-surface-gray-1 border-outline-gray-1.5 text-ink-gray-5',
}

const logStatus: Record<string, Status> = {
  queued: 'waiting',
  running: 'running',
  success: 'done',
  failed: 'failed',
  killed: 'failed',
}

const backupStatus: Record<MigrationSite['backup_status'], Status> = {
  pending: 'waiting',
  backing_up: 'running',
  backed_up: 'done',
  failed: 'failed',
}

const appStepStatus: Record<MigrationApp['validate_status'], Status> = {
  pending: 'waiting',
  running: 'running',
  success: 'done',
  failed: 'failed',
  skipped: 'skipped',
}

const migrateStatus: Record<MigrationSite['migration_status'], Status> = {
  pending: 'waiting',
  running: 'running',
  success: 'done',
  failed: 'failed',
  recovering: 'unknown',
  recovered: 'unknown',
}

const chain = computed(() => props.op.chain)
const logs = computed(() => new Map(props.op.task_logs.map((log) => [log.id, log])))

const activity = computed(
  () => props.op.task_logs.find((log) => log.status === 'running' && log.activity)?.activity,
)

const span = (links: MigrationChainLink[]) => {
  if (!links.length || links.some((link) => !link.started_at || !link.finished_at)) return ''

  const seconds = links.reduce(
    (sum, link) => sum + (Date.parse(link.finished_at || '') - Date.parse(link.started_at || '')) / 1000,
    0,
  )

  return fmtDuration(seconds)
}

const linksOf = (command: string) => chain.value.filter((link) => link.command === command)

const isRestoring = computed(
  () =>
    ['reverting_apps', 'reverting_sites', 'restarting', 'revert_failed', 'reverted'].includes(
      props.op.state,
    ) || chain.value.some((link) => link.command.startsWith('revert')),
)

const idle = computed<Status>(() =>
  isRestoring.value || ['dismissed', 'stopped'].includes(props.op.state) ? 'skipped' : 'waiting',
)

const isIdle = (status: Status) => status === 'waiting' || status === 'skipped'

const settle = (status: Status) => (status === 'waiting' ? idle.value : status)

const retained = (taskId: string | undefined) => (taskId && logs.value.has(taskId) ? taskId : null)

const fromLog = (taskId: string) => {
  const status = logs.value.get(taskId)?.status

  return status ? logStatus[status] : 'unknown'
}

const aggregate = (statuses: Status[]): Status => {
  if (statuses.includes('failed')) return 'failed'
  if (statuses.includes('stopped')) return 'stopped'
  if (statuses.includes('running')) return 'running'
  if (statuses.every(isIdle)) return idle.value

  return 'done'
}

const siteRow = (command: string, site: string, status: Status): Row => {
  const links = chain.value.filter((link) => link.command === command && link.site === site)
  const last = links.length - 1

  return {
    id: `${command}-${site}`,
    label: site,
    status: settle(status),
    taskId: retained(links[last]?.task_id),
    time: links[last] ? span([links[last]]) : '',
    children:
      links.length > 1
        ? links.map((link, index) => ({
            id: link.task_id,
            label: `Attempt ${index + 1}`,
            status: index === last ? settle(status) : fromLog(link.task_id),
            taskId: retained(link.task_id),
            time: span([link]),
          }))
        : [],
  }
}

const siteStage = (
  key: string,
  name: string,
  command: string,
  statusOf: (site: MigrationSite) => Status,
): Stage => {
  const rows = props.op.sites.map((site) => siteRow(command, site.name, statusOf(site)))

  return {
    key,
    name,
    status: aggregate(rows.map((row) => row.status)),
    time: span(linksOf(command)),
    panels: rows.length ? [{ key, title: '', noun: 'sites', rows }] : [],
    summary: '',
  }
}

const appsStatus = computed<Status>(() => {
  if (props.op.apps_updated) return 'done'
  if (props.op.state === 'updating') return 'running'

  const hasFailed =
    props.op.diagnosis?.phase === 'update' ||
    (chain.value.some((link) => link.command === 'revert-apps') &&
      !chain.value.some((link) => link.command === 'migrate'))

  return hasFailed ? 'failed' : idle.value
})

const shortSha = (sha: string | null) => sha?.slice(0, 7) || '-'

const revisionOf = (app: MigrationApp): Revision => ({
  from: shortSha(app.sha),
  to: shortSha(app.updated_sha || app.target_sha),
  url: app.compare_url,
})

const isTracked = computed(() =>
  props.op.apps.some((app) => app.validate_status && app.validate_status !== 'pending'),
)

const appRows = (step: 'validate' | 'update' | null) => {
  const taskId = retained(linksOf('update').at(-1)?.task_id)

  return props.op.apps.map((app) => {
    const recorded = step ? app[`${step}_status`] : null
    const status = recorded && recorded !== 'pending' ? appStepStatus[recorded] : appsStatus.value

    return {
      id: `${step}-${app.name}`,
      label: titleMap.value[app.name] || app.name,
      status: settle(status),
      taskId,
      time: '',
      children: [],
      revision: step === 'validate' ? undefined : revisionOf(app),
    }
  })
}

const appsStage = computed<Stage>(() => {
  const panels = isTracked.value
    ? (['validate', 'update'] as const).map((step) => ({
        key: `update-${step}`,
        title: step === 'validate' ? 'Validate' : 'Update',
        noun: 'apps',
        rows: appRows(step),
      }))
    : [{ key: 'update-apps', title: '', noun: 'apps', rows: appRows(null) }]

  return {
    key: 'update',
    name: 'Update apps',
    status: appsStatus.value,
    time: span(linksOf('update')),
    panels,
    summary: '',
  }
})

const restartStatus = computed<Status>(() => {
  if (props.op.state === 'completed') return 'done'

  const link = chain.value.find((item) => item.command === 'complete-migration')
  const sitesDone = props.op.sites.every((site) => site.migration_status === 'success')
  const hasFailed =
    props.op.state === 'needs_attention' &&
    props.op.return_state === 'migrating' &&
    !props.op.failed_site &&
    sitesDone

  if (hasFailed) return 'failed'

  return link ? fromLog(link.task_id) : idle.value
})

const migratedStatus = (site: MigrationSite): Status => {
  if (!['recovering', 'recovered'].includes(site.migration_status)) {
    return migrateStatus[site.migration_status]
  }

  const last = linksOf('migrate').filter((link) => link.site === site.name).at(-1)

  return last ? fromLog(last.task_id) : 'done'
}

const runStages = computed(() => {
  const list: Stage[] = []

  if (!props.op.safeguards_disabled && props.op.sites.length) {
    list.push(
      siteStage(
        'backup',
        'Back up',
        'migration-backup',
        (site) =>
          props.op.state === 'stopped' && site.backup_status === 'backing_up'
            ? 'stopped'
            : backupStatus[site.backup_status],
      ),
    )
  }

  if (props.op.kind === 'update' && props.op.apps.length) list.push(appsStage.value)

  list.push(
    siteStage('migrate', 'Migrate', 'migrate', migratedStatus),
  )

  if (isRestoring.value) return list

  list.push({
    key: 'restart',
    name: 'Restart',
    status: restartStatus.value,
    time: span(linksOf('complete-migration')),
    panels: [],
    summary: 'Web and workers',
  })

  return list
})

const restoreStages = computed<Stage[]>(() => {
  if (!isRestoring.value) return []

  const checkpoints = props.op.revert_checkpoints
  const isFailed = props.op.state === 'revert_failed'
  const step = (isDone: boolean, isCurrent: boolean): Status => {
    if (isDone) return 'done'
    if (isCurrent) return isFailed ? 'failed' : 'running'

    return 'waiting'
  }

  const list: Stage[] = []
  const revertSites = [...new Map(linksOf('revert-site').map((link) => [link.site, link])).values()]

  if (linksOf('revert-apps').length) {
    const status = step(
      !!checkpoints.apps,
      props.op.state === 'reverting_apps' || (isFailed && !checkpoints.apps),
    )
    const taskId = retained(linksOf('revert-apps').at(-1)?.task_id)
    const rows = props.op.apps.map((app) => ({
      id: `revert-${app.name}`,
      label: titleMap.value[app.name] || app.name,
      status,
      taskId,
      time: '',
      children: [],
    }))

    list.push({
      key: 'revert-apps',
      name: 'Revert apps',
      status,
      time: span(linksOf('revert-apps')),
      panels: [{ key: 'revert-apps', title: '', noun: 'apps', rows }],
      summary: '',
    })
  }

  if (revertSites.length) {
    const isActive = props.op.state === 'reverting_sites' || (isFailed && !!checkpoints.apps)
    const current = revertSites.find((link) => !checkpoints[`site:${link.site}`])
    const rows = revertSites.map((link) => ({
      id: `revert-site-${link.site}`,
      label: link.site || '',
      status: step(!!checkpoints[`site:${link.site}`], isActive && link === current),
      taskId: retained(link.task_id),
      time: span([link]),
      children: [],
    }))

    list.push({
      key: 'restore-backups',
      name: 'Restore backups',
      status: aggregate(rows.map((row) => row.status)),
      time: span(revertSites),
      panels: [{ key: 'restore-backups', title: '', noun: 'sites', rows }],
      summary: '',
    })
  }

  list.push({
    key: 'restart-services',
    name: 'Restart services',
    status: step(
      !!checkpoints.restarted || props.op.state === 'reverted',
      props.op.state === 'restarting',
    ),
    time: span(linksOf('restart-services')),
    panels: [],
    summary: 'Web and workers',
  })

  return list
})

const stages = computed(() => [...runStages.value, ...restoreStages.value])

const stageNote = (stage: Stage) => {
  if (stage.status === 'skipped') return 'Skipped'
  if (stage.status === 'waiting') return 'Waiting'

  return stage.time
}

const pastTense: Record<string, string> = {
  backup: 'backed up',
  update: 'updated',
  migrate: 'migrated',
  'revert-apps': 'reverted',
  'restore-backups': 'restored',
}

const stageRows = (stage: Stage) => {
  const panel = stage.panels.at(-1)

  return panel ? panel.rows : []
}

const outcome = (stage: Stage) => {
  if (stage.key === 'restart' || stage.key === 'restart-services') {
    if (stage.status === 'done') return 'Web and workers restarted'
    if (stage.status === 'running') return 'Restarting web and workers'
    if (stage.status === 'failed') return 'Web and workers did not restart'

    return 'Web and workers'
  }

  const rows = stageRows(stage)
  const noun = stage.key === 'update' || stage.key === 'revert-apps' ? 'app' : 'site'
  const subject = `${rows.length} ${noun}${rows.length === 1 ? '' : 's'}`
  const finished = rows.filter((row) => row.status === 'done').length

  if (stage.status === 'done') return `${subject} ${pastTense[stage.key]}`
  if (stage.status === 'running') return `${finished} of ${subject} ${pastTense[stage.key]}`
  if (stage.status === 'failed' || stage.status === 'stopped') {
    return `${finished} of ${subject} ${pastTense[stage.key]}, then stopped`
  }

  return subject
}

const subtext = (stage: Stage) => [outcome(stage), stageNote(stage)].filter(Boolean).join(' · ')

const failedKey = computed(() => stages.value.find((stage) => stage.status === 'failed')?.key)

const threadTone = (next?: Stage) =>
  next && isIdle(next.status) ? '[--step-thread-color:var(--outline-gray-1-5)]' : ''

const matrixLine = (panel: Panel) => {
  const done = panel.rows.filter((row) => row.status === 'done').length
  const total = panel.rows.length
  const failed = panel.rows.filter((row) => row.status === 'failed').length

  if (failed) return `${done} of ${total}, ${failed} failed`
  if (panel.rows.some((row) => row.status === 'running')) return `${done} of ${total} done`

  const noun = total === 1 ? panel.noun.slice(0, -1) : panel.noun

  if (panel.rows.every((row) => isIdle(row.status))) return `${total} ${noun}`

  return `${total} ${noun} done`
}

const rowInk = (status: Status) => {
  if (status === 'failed') return 'text-ink-red-6'

  return isIdle(status) ? 'text-ink-gray-5' : 'text-ink-gray-7'
}

const toggled = (set: Set<string>, key: string) => {
  const next = new Set(set)

  if (!next.delete(key)) next.add(key)

  return next
}

const openTask = (taskId: string) => router.push({ name: 'TaskDetail', params: { taskId } })

watch(
  () => props.op.id,
  () => {
    const isBig = props.op.sites.length > 6
    const failed = stages.value
      .filter((stage) => stage.status === 'failed' || stage.status === 'running')
      .map((stage) => stage.key)

    const open = stages.value.filter(
      (stage) =>
        stage.panels.length && stage.status !== 'skipped' && (!isBig || failed.includes(stage.key)),
    )

    shown.value = new Set(open.map((stage) => stage.key))
    expanded.value = new Set()
  },
  { immediate: true },
)

watch(
  () =>
    stages.value
      .filter((stage) => stage.status === 'running' || stage.status === 'failed')
      .map((stage) => stage.key)
      .join(),
  () => {
    const active = stages.value.filter(
      (stage) => stage.status === 'running' || stage.status === 'failed',
    )

    shown.value = new Set([...shown.value, ...active.map((stage) => stage.key)])
  },
)
</script>

<template>
  <section>
    <slot v-if="!failedKey" name="failure" />

    <ol class="[&:not(:first-child)]:mt-4">
      <li
        v-for="(stage, index) in stages"
        :key="stage.key"
        class="relative grid grid-cols-[36px_minmax(0,1fr)] gap-x-3 pb-8 last:pb-0 step-thread"
        :class="threadTone(stages[index + 1])"
      >
        <span
          class="relative place-items-center grid border rounded-5 size-9"
          :class="[tileTone[stage.status], stage.status === 'running' && 'status-ring']"
        >
          <span
            v-if="typeof stageIcon[stage.key] === 'string'"
            class="size-4"
            :class="stageIcon[stage.key]"
          />

          <component :is="stageIcon[stage.key]" v-else class="size-4" />
        </span>

        <div class="min-w-0">
          <button
            type="button"
            class="group flex items-center gap-4 w-full min-h-9 enabled:cursor-pointer text-left"
            :disabled="!stage.panels.length"
            :aria-expanded="stage.panels.length ? shown.has(stage.key) : undefined"
            @click="shown = toggled(shown, stage.key)"
          >
            <span class="flex-1 min-w-0">
              <span
                class="block text-base-medium truncate"
                :class="isIdle(stage.status) ? 'text-ink-gray-5' : 'text-ink-gray-8 group-[:enabled:hover]:text-ink-gray-9'"
              >
                {{ stage.name }}
              </span>

              <span class="block mt-1 text-ink-gray-5 group-[:enabled:hover]:text-ink-gray-6 text-sm truncate">
                {{ subtext(stage) }}
              </span>

              <span v-if="stage.status === 'running' && activity" class="block mt-1.5 text-ink-gray-6 text-sm truncate">
                {{ activity }}
              </span>
            </span>

            <span
              v-if="stage.panels.length"
              class="mr-1.5 size-4 text-ink-gray-5 group-hover:text-ink-gray-7 transition-transform lucide-chevron-down shrink-0"
              :class="shown.has(stage.key) && 'rotate-180'"
              aria-hidden="true"
            />
          </button>

          <slot v-if="stage.key === failedKey" name="failure" />

          <div
            v-if="stage.panels.length"
            class="grid transition-[grid-template-rows] duration-200 motion-reduce:transition-none"
            :class="shown.has(stage.key) ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'"
          >
            <div class="overflow-hidden">
              <div class="space-y-2 bg-surface-gray-0 mt-4 px-4 py-1.5 border border-outline-gray-1.5 rounded-5">
                <section v-for="panel in stage.panels" :key="panel.key">
                  <h3 v-if="panel.title" class="flex items-baseline gap-2 pt-1.5 h-7 text-ink-gray-5 text-sm">
                    <span class="text-ink-gray-7 text-base-medium">{{ panel.title }}</span>
                    {{ matrixLine(panel) }}
                  </h3>

                  <ul class="max-h-[320px] overflow-y-auto">
            <li v-for="row in panel.rows" :key="row.id">
              <Collapsable
                :opened="expanded.has(row.id)"
                @update:opened="expanded = toggled(expanded, row.id)"
              >
                <template #header="{ opened, toggle }">
                  <div class="flex items-center gap-2 h-8 text-base">
                    <Spinner v-if="row.status === 'running'" class="size-3.5 text-ink-amber-6 shrink-0" />
                    <span v-else class="size-3.5 shrink-0" :class="mark[row.status]" />
                    <span class="min-w-12 truncate" :class="rowInk(row.status)">{{ row.label }}</span>

                    <component
                      :is="row.revision.url ? 'a' : 'span'"
                      v-if="row.revision"
                      :href="row.revision.url || undefined"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="flex items-center gap-1 ml-auto font-mono text-ink-gray-5 hover:text-ink-gray-7 text-xs shrink-0"
                    >
                      {{ row.revision.from }}
                      <span class="size-3 text-ink-gray-4 lucide-arrow-right shrink-0" aria-hidden="true" />
                      <span class="text-ink-gray-6">{{ row.revision.to }}</span>
                      <span v-if="row.revision.url" class="size-3 text-ink-gray-4 lucide-arrow-up-right shrink-0" aria-hidden="true" />
                    </component>

                    <Button
                      v-if="row.children.length"
                      class="shrink-0"
                      size="xs"
                      variant="ghost"
                      :label="`×${row.children.length}`"
                      :icon-right="opened ? 'lucide-chevron-up' : 'lucide-chevron-down'"
                      @click="toggle"
                    />

                    <span v-if="row.time" class="ml-auto tabular-nums text-ink-gray-5 text-sm shrink-0">
                      {{ row.time }}
                    </span>

                    <Tooltip v-if="row.taskId && !row.children.length" text="Open task">
                      <Button
                        class="shrink-0"
                        size="sm"
                        variant="ghost"
                        icon="lucide-arrow-up-right"
                        label="Open task"
                        @click="openTask(row.taskId)"
                      />
                    </Tooltip>
                  </div>
                </template>

                <ol v-if="row.children.length" class="ml-1.5 pl-3 border-l border-outline-gray-3">
                  <li
                    v-for="child in row.children"
                    :key="child.id"
                    class="flex items-center gap-2 h-7 text-base"
                  >
                    <Spinner v-if="child.status === 'running'" class="size-3 text-ink-amber-6 shrink-0" />
                    <span v-else class="size-3 shrink-0" :class="mark[child.status]" />

                    <span :class="child.status === 'failed' ? 'text-ink-red-6' : 'text-ink-gray-6'">
                      {{ child.label }}
                    </span>

                    <span class="ml-auto tabular-nums text-ink-gray-5">{{ child.time }}</span>

                    <Tooltip v-if="child.taskId" text="Open task">
                      <Button
                        class="shrink-0"
                        size="sm"
                        variant="ghost"
                        icon="lucide-arrow-up-right"
                        label="Open task"
                        @click="openTask(child.taskId)"
                      />
                    </Tooltip>
                  </li>
                </ol>
              </Collapsable>
            </li>
                  </ul>
                </section>
              </div>
            </div>
          </div>
        </div>
      </li>
    </ol>
  </section>
</template>
