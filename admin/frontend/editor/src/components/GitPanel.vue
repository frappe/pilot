<script setup lang="ts">
import { Button, Dropdown, dialog, Textarea, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { gitApi } from '@/api/git'
import FileIcon from '@/components/FileIcon.vue'
import PanelHeader from '@/components/ui/PanelHeader.vue'
import { usePrompt } from '@/composables/usePrompt'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useTreeStore } from '@/stores/tree'
import { baseName, dirName, relTime } from '@/utils'

interface Commit {
  sha: string
  short: string
  subject: string
  author: string
  time: number
}

const emit = defineEmits<{ close: [] }>()

const editor = useEditorStore()
const git = useGitStore()
const tree = useTreeStore()
const { prompt } = usePrompt()

const message = ref('')
const committing = ref(false)
const pushing = ref(false)
const pulling = ref(false)
const syncing = ref(false)
const branchList = ref<string[]>([])
const showHistory = ref(false)
const commits = ref<Commit[]>([])
const moreCommits = ref(false)
const loadingCommits = ref(false)

const commitPage = 30

const statusColors: Record<string, string> = {
  U: 'text-ink-green-5',
  A: 'text-ink-green-5',
  D: 'text-ink-red-7',
}

const letter = (code: string) => (code.includes('?') ? 'U' : code[0])

const statusColor = (code: string) => statusColors[letter(code)] || 'text-ink-amber-5'

const loadBranches = async () => {
  branchList.value = (await git.branches()).branches
}

const loadCommits = async (reset = true) => {
  loadingCommits.value = true

  try {
    const skip = reset ? 0 : commits.value.length
    const d = await git.log(skip, commitPage)
    const list: Commit[] = d.commits || []

    commits.value = reset ? list : commits.value.concat(list)
    moreCommits.value = !!d.more
  } finally {
    loadingCommits.value = false
  }
}

const toggleHistory = () => {
  showHistory.value = !showHistory.value

  if (showHistory.value) loadCommits()
}

const stage = async (path: string) => {
  await git.stage(path)
}

const unstage = async (path: string) => {
  await git.unstage(path)
}

const discard = (path: string) => {
  dialog.danger({
    title: 'Discard changes',
    message: `Discard changes to ${baseName(path)}?`,
    confirmLabel: 'Discard',
    onConfirm: async () => {
      const r = await git.discard(path)

      if (r?.ok === false) toast.error(r.message || 'Discard failed')
      else if (editor.diff?.path === path) editor.closeDiff()
    },
  })
}

const stageAll = async () => {
  for (const f of git.unstaged) await gitApi.stage(f.path)

  await git.refresh()
}

const unstageAll = async () => {
  for (const f of git.staged) await gitApi.unstage(f.path)

  await git.refresh()
}

const commit = async () => {
  if (!message.value.trim()) return

  committing.value = true

  try {
    const all = git.staged.length === 0
    const r = await git.commit(message.value.trim(), all)

    if (r?.ok === false) toast.error(r.message || 'Commit failed')
    else {
      message.value = ''

      if (showHistory.value) loadCommits()
    }
  } finally {
    committing.value = false
  }
}

const switchBranch = async (branch: string, create: boolean) => {
  const r = await git.checkout(branch, create)

  if (r?.ok === false) {
    toast.error(r.message || 'Checkout failed')
    return
  }

  await loadBranches()

  if (showHistory.value) loadCommits()

  tree.reload('')
  // Open files now point at the other branch's content. Only reload saved tabs:
  // refreshTab drops unsaved edits, and a checkout is no reason to lose them.
  for (const tab of editor.tabs) {
    if (!tab.dirty) editor.refreshTab(tab.path).catch(() => {})
  }
}

const createBranch = async () => {
  const name = await prompt('New branch name', '', 'feature/...')

  if (name) switchBranch(name, true)
}

const push = async () => {
  pushing.value = true

  let r: Awaited<ReturnType<typeof git.push>> | undefined

  try {
    r = await git.push(false)
  } finally {
    pushing.value = false
  }

  if (r?.ok !== false) return

  dialog.danger({
    title: 'Force push',
    message: `${r.message} Force push?`,
    confirmLabel: 'Force push',
    onConfirm: async () => {
      pushing.value = true

      try {
        const fr = await git.push(true)

        if (fr?.ok === false) toast.error(fr.message || 'Force push failed')
      } finally {
        pushing.value = false
      }
    },
  })
}

const pull = async () => {
  pulling.value = true

  try {
    const r = await git.pull()

    if (r?.ok === false) {
      toast.error(r.message || 'Pull failed')
      return false
    }

    if (showHistory.value) loadCommits()

    return true
  } finally {
    pulling.value = false
  }
}

// Pull before push, so a sync on a diverged branch does not just fail the push.
const sync = async () => {
  syncing.value = true

  try {
    if (git.behind && !(await pull())) return

    await push()
  } finally {
    syncing.value = false
  }
}

const refresh = () => {
  git.refresh()
  loadBranches()

  if (showHistory.value) loadCommits()
}

const commitPlaceholder = computed(() =>
  git.staged.length ? 'Message for staged changes' : 'Message (stages everything)',
)

const commitLabel = computed(() => {
  const n = git.staged.length || git.unstaged.length

  return `Commit${n ? ` (${n})` : ''}`
})

const hasChanges = computed(() => git.staged.length > 0 || git.unstaged.length > 0)
const hasRemoteWork = computed(() => git.ahead > 0 || git.behind > 0)

const trackingLabel = computed(() => {
  const parts: string[] = []

  if (git.behind) parts.push(`↓${git.behind}`)

  if (git.ahead) parts.push(`↑${git.ahead}`)

  return parts.join(' ')
})

const trackingTitle = computed(() =>
  [
    git.behind ? `${git.behind} commit(s) to pull` : '',
    git.ahead ? `${git.ahead} commit(s) to push` : '',
  ]
    .filter(Boolean)
    .join(', '),
)

const primary = computed(() => {
  if (!hasChanges.value && hasRemoteWork.value) {
    return {
      label: `Sync Changes${trackingLabel.value ? ` ${trackingLabel.value}` : ''}`,
      icon: 'lucide-refresh-cw',
      disabled: syncing.value,
      loading: syncing.value,
      run: sync,
    }
  }

  return {
    label: commitLabel.value,
    icon: 'lucide-check',
    disabled: !message.value.trim() || committing.value,
    loading: committing.value,
    run: commit,
  }
})

const remoteOptions = computed(() => [
  { label: 'Sync Changes', icon: 'lucide-refresh-cw', onClick: sync },
  { label: 'Pull', icon: 'lucide-arrow-down', onClick: pull },
  { label: 'Push', icon: 'lucide-arrow-up', onClick: push },
])

// Staged and unstaged rows differ only in their actions, so describe them once.
const fileSections = computed(() => [
  {
    id: 'staged',
    title: 'Staged Changes',
    files: git.staged,
    bulkIcon: 'lucide-minus',
    bulkTitle: 'Unstage all',
    bulk: unstageAll,
    actions: [{ icon: 'lucide-minus', title: 'Unstage', run: unstage }],
  },
  {
    id: 'unstaged',
    title: 'Changes',
    files: git.unstaged,
    bulkIcon: 'lucide-plus',
    bulkTitle: 'Stage all',
    bulk: stageAll,
    actions: [
      { icon: 'lucide-undo-2', title: 'Discard changes', run: discard },
      { icon: 'lucide-plus', title: 'Stage', run: stage },
    ],
  },
])

// git.branch is the one owner of "where we are"; the dropdown only lists what
// else we could check out.
// Kept short: the header shares its width with the panel title.
const branchLabel = computed(() => (git.branch === 'HEAD' ? 'Detached' : git.branch || 'No branch'))

const branchTitle = computed(() =>
  [`On ${branchLabel.value}`, trackingTitle.value].filter(Boolean).join(' · '),
)

const branchItems = computed(() =>
  branchList.value.map((name) => ({
    label: name,
    icon: 'lucide-git-branch',
    selected: name === git.branch,
    onClick: () => name !== git.branch && switchBranch(name, false),
  })),
)

const branchMenu = computed(() => [
  { group: 'Branches', options: branchItems.value },
  {
    group: 'Create',
    hideLabel: true,
    options: [{ label: 'Create new branch…', icon: 'lucide-plus', onClick: createBranch }],
  },
  { group: 'Remote', options: remoteOptions.value },
])

onMounted(() => {
  if (!git.loaded) git.refresh()

  loadBranches()
})
</script>

<template>
  <div class="flex h-full flex-col">
    <PanelHeader title="Source Control" @close="emit('close')">
      <template v-if="git.repo" #context>
        <Dropdown :options="branchMenu" align="end">
          <template #default="{ open }">
            <Button class="min-w-0" variant="ghost" :title="branchTitle" @click="loadBranches">
              <span class="ed-name">{{ branchLabel }}</span>
              <template #suffix>
                <span v-if="trackingLabel" class="ed-meta tabular-nums">{{ trackingLabel }}</span>
                <span
                  class="size-4 shrink-0 text-ink-gray-5 transition-transform lucide-chevron-down"
                  :class="{ 'rotate-180': open }"
                ></span>
              </template>
            </Button>
          </template>
        </Dropdown>
        <button class="ed-icon-button ml-1 shrink-0" title="Refresh" @click="refresh">
          <span class="lucide-refresh-cw h-4 w-4 text-current"></span>
        </button>
      </template>
    </PanelHeader>

    <div v-if="!git.repo" class="ed-empty">Not a git repository.</div>

    <template v-else>
      <!-- Only what is usable right now: the message box appears when there is
           something to commit, the action when there is anything to do at all. -->
      <div v-if="hasChanges || hasRemoteWork" class="flex flex-col items-end gap-2 px-3 pb-3">
        <Textarea
          v-if="hasChanges"
          class="w-full"
          v-model="message"
          :rows="2"
          :placeholder="commitPlaceholder"
          @keydown.ctrl.enter.prevent="commit"
          @keydown.meta.enter.prevent="commit"
        />
        <Button
          variant="solid"
          :icon-left="primary.icon"
          :label="primary.label"
          :disabled="primary.disabled"
          :loading="primary.loading"
          @click="primary.run()"
        />
      </div>

      <div class="min-h-0 flex-1 overflow-auto pb-24 sm:pb-2">
        <template v-for="section in fileSections" :key="section.id">
          <div v-if="section.files.length" class="ed-section">
            <span>{{ section.title }}</span>
            <span class="tabular-nums">{{ section.files.length }}</span>
            <Button
              class="ml-auto"
              variant="ghost"
              :icon="section.bulkIcon"
              :title="section.bulkTitle"
              @click="section.bulk()"
            />
          </div>
          <div class="px-1.5">
            <div
              v-for="file in section.files"
              :key="section.id + file.path"
              class="group/row ed-row"
              :title="file.path"
              @click="editor.openDiff(file.path, section.id === 'staged')"
            >
              <FileIcon :name="baseName(file.path)" class="ed-lane" />
              <span class="ed-name">{{ baseName(file.path) }}</span>
              <span class="ed-path">{{ dirName(file.path) }}</span>
              <div
                class="ml-auto flex items-center transition sm:opacity-0 sm:group-focus-within/row:opacity-100 sm:group-hover/row:opacity-100"
              >
                <Button
                  v-for="action in section.actions"
                  :key="action.title"
                  variant="ghost"
                  :icon="action.icon"
                  :title="action.title"
                  @click.stop="action.run(file.path)"
                />
              </div>
              <span class="ed-lane text-xs-semibold" :class="statusColor(file.code)">
                {{ letter(file.code) }}
              </span>
            </div>
          </div>
        </template>

        <div v-if="!git.staged.length && !git.unstaged.length" class="ed-empty">
          No changes in this working tree.
        </div>

        <button class="ed-section w-full hover:text-ink-gray-7" @click="toggleHistory">
          <span
            class="lucide-chevron-right h-3.5 w-3.5 transition-transform"
            :class="{ 'rotate-90': showHistory }"
          ></span>
          Commits
        </button>
        <div v-if="showHistory" class="px-1.5">
          <div
            v-for="c in commits"
            :key="c.sha"
            class="ed-row h-auto py-1.5"
            :title="c.subject"
            @click="editor.openCommit(c.sha)"
          >
            <span class="lucide-git-commit-horizontal ed-lane self-start text-ink-gray-4"></span>
            <div class="min-w-0 flex-1">
              <div class="ed-name">{{ c.subject }}</div>
              <div class="ed-path">{{ c.author }} · {{ relTime(c.time) }}</div>
            </div>
            <code class="ed-meta font-mono">{{ c.short }}</code>
          </div>
          <div v-if="!commits.length && !loadingCommits" class="ed-empty">No commits yet.</div>
          <Button
            v-if="moreCommits"
            class="w-full"
            variant="ghost"
            :loading="loadingCommits"
            label="Load more"
            @click="loadCommits(false)"
          />
        </div>
      </div>
    </template>
  </div>
</template>
