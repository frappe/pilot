<template>
  <div class="flex h-full flex-col">
    <PanelHeader title="Source Control" @close="emit('close')">
      <template #actions>
        <button class="ed-icon-button" title="Refresh" @click="refresh">
          <span class="lucide-refresh-cw h-4 w-4 text-current"></span>
        </button>
      </template>
    </PanelHeader>

    <div v-if="!git.repo" class="ed-empty">Not a git repository.</div>

    <template v-else>
      <div class="flex flex-col gap-2 px-3 pb-3">
        <div class="flex items-center gap-1">
          <!-- Dropdown does not forward classes, so the flex sizing lives on a wrapper. -->
          <div class="min-w-0 flex-1">
            <Dropdown :options="branchOptions" placement="left">
              <Button class="w-full justify-start" icon-left="lucide-git-branch" :label="git.branch" />
            </Dropdown>
          </div>
          <span v-if="trackingLabel" class="ed-meta shrink-0 tabular-nums" :title="trackingTitle">
            {{ trackingLabel }}
          </span>
          <Dropdown :options="remoteOptions" placement="right">
            <Button class="shrink-0" variant="ghost" icon="lucide-ellipsis" title="Remote actions" />
          </Dropdown>
        </div>

        <Textarea
          v-model="message"
          :rows="2"
          :placeholder="commitPlaceholder"
          @keydown.ctrl.enter.prevent="commit"
          @keydown.meta.enter.prevent="commit"
        />
        <!-- One primary action, as in VS Code: commit while there is something to
             commit, otherwise sync whatever is waiting on the remote. -->
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
              <span class="ed-lane text-xs font-semibold" :class="statusColor(file.code)">
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

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, Dropdown, Textarea, confirmDialog, toast } from 'frappe-ui'
import FileIcon from '@/components/FileIcon.vue'
import PanelHeader from '@/components/ui/PanelHeader.vue'
import { gitApi } from '@/api/git'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useTreeStore } from '@/stores/tree'
import { usePrompt } from '@/composables/usePrompt'
import { baseName, dirName, relTime } from '@/utils'

const emit = defineEmits(['close'])

const editor = useEditorStore()
const git = useGitStore()
const tree = useTreeStore()
const { prompt } = usePrompt()

const message = ref('')
const committing = ref(false)
const pushing = ref(false)
const pulling = ref(false)
const syncing = ref(false)
const branchList = ref([])
const branchCurrent = ref('')
const showHistory = ref(false)
const commits = ref([])
const moreCommits = ref(false)
const loadingCommits = ref(false)

const COMMIT_PAGE = 30

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
  const parts = []
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
      { icon: 'lucide-rotate-ccw', title: 'Discard changes', run: discard },
      { icon: 'lucide-plus', title: 'Stage', run: stage },
    ],
  },
])

const branchOptions = computed(() => {
  const items = branchList.value.map((b) => ({
    label: b,
    icon: b === branchCurrent.value ? 'lucide-check' : 'lucide-git-branch',
    onClick: () => b !== branchCurrent.value && switchBranch(b, false),
  }))
  items.push({ label: 'Create new branch', icon: 'lucide-plus', onClick: createBranch })
  return items
})

function letter(code) {
  return code.includes('?') ? 'U' : code[0]
}
function statusColor(code) {
  return { U: 'text-ink-green-3', A: 'text-ink-green-3', D: 'text-ink-red-4' }[letter(code)] || 'text-ink-amber-3'
}

async function loadBranches() {
  const { current, branches } = await git.branches()
  branchCurrent.value = current
  branchList.value = branches
}

async function loadCommits(reset = true) {
  loadingCommits.value = true
  try {
    const skip = reset ? 0 : commits.value.length
    const d = await git.log(skip, COMMIT_PAGE)
    const list = d.commits || []
    commits.value = reset ? list : commits.value.concat(list)
    moreCommits.value = !!d.more
  } finally {
    loadingCommits.value = false
  }
}

function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value) loadCommits()
}

async function stage(path) {
  await git.stage(path)
}
async function unstage(path) {
  await git.unstage(path)
}
function discard(path) {
  confirmDialog({
    title: 'Discard changes',
    message: `Discard changes to ${baseName(path)}?`,
    onConfirm: async ({ hideDialog }) => {
      hideDialog()
      const r = await git.discard(path)
      if (r?.ok === false) toast.error(r.message || 'Discard failed')
      else if (editor.diff?.path === path) editor.closeDiff()
    },
  })
}
async function stageAll() {
  for (const f of git.unstaged) await gitApi.stage(f.path)
  await git.refresh()
}
async function unstageAll() {
  for (const f of git.staged) await gitApi.unstage(f.path)
  await git.refresh()
}

async function commit() {
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

async function switchBranch(branch, create) {
  const r = await git.checkout(branch, create)
  if (r?.ok === false) {
    toast.error(r.message || 'Checkout failed')
    return
  }
  await loadBranches()
  if (showHistory.value) loadCommits()
  tree.reload('')
}

async function createBranch() {
  const name = await prompt('New branch name', '', 'feature/...')
  if (name) switchBranch(name, true)
}

async function push() {
  pushing.value = true
  let r
  try {
    r = await git.push(false)
  } finally {
    pushing.value = false
  }
  if (r?.ok !== false) return
  confirmDialog({
    title: 'Force push',
    message: `${r.message} Force push?`,
    onConfirm: async ({ hideDialog }) => {
      hideDialog()
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

async function pull() {
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
async function sync() {
  syncing.value = true
  try {
    if (git.behind && !(await pull())) return
    await push()
  } finally {
    syncing.value = false
  }
}

function refresh() {
  git.refresh()
  loadBranches()
  if (showHistory.value) loadCommits()
}

onMounted(() => {
  if (!git.loaded) git.refresh()
  loadBranches()
})
</script>
