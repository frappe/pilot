<template>
  <div class="flex h-full flex-col">
    <div
      class="group flex h-11 shrink-0 items-center justify-between border-b border-outline-gray-1 px-3 sm:h-9 sm:border-b-0">
      <span class="text-sm font-semibold text-ink-gray-8 sm:text-2xs sm:uppercase sm:tracking-wider sm:text-ink-gray-5">
        Source Control
      </span>
      <div class="flex items-center gap-0.5">
        <button
          class="rounded p-1.5 text-ink-gray-6 transition hover:bg-surface-gray-2 sm:p-1 sm:opacity-0 sm:group-hover:opacity-100"
          title="Refresh" @click="refresh">
          <span class="lucide-refresh-cw h-[18px] w-[18px] text-current sm:h-3.5 sm:w-3.5"></span>
        </button>
        <button class="ml-1 rounded p-1.5 text-ink-gray-6 hover:bg-surface-gray-2 sm:hidden" aria-label="Close"
          @click="emit('close')">
          <span class="lucide-x h-[18px] w-[18px] text-current"></span>
        </button>
      </div>
    </div>

    <div v-if="!git.repo" class="px-3 py-2 text-sm text-ink-gray-4 sm:text-xs">
      Not a git repository.
    </div>

    <template v-else>
      <div class="px-3 pb-2">
        <div class="flex items-center gap-2">
          <div class="min-w-0 flex-1">
            <Dropdown :options="branchOptions" placement="left">
              <button
                class="flex w-full items-center gap-1.5 rounded-md border border-outline-gray-2 px-2 py-2 text-sm text-ink-gray-7 hover:bg-surface-gray-2 sm:py-1 sm:text-xs"
                title="Switch branch">
                <span class="lucide-git-branch h-4 w-4 flex-shrink-0 text-current sm:h-3.5 sm:w-3.5"></span>
                <span class="min-w-0 truncate font-medium text-ink-gray-8">{{ git.branch }}</span>
                <span
                  class="lucide-chevron-down ml-auto h-4 w-4 flex-shrink-0 text-ink-gray-4 sm:h-3.5 sm:w-3.5"></span>
              </button>
            </Dropdown>
          </div>
          <div class="flex items-center gap-1">
            <button
              class="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-gray-6 transition hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="pulling" title="Pull from remote" @click="pull">
              <span v-if="pulling" class="lucide-loader-2 h-3.5 w-3.5 animate-spin"></span>
              <span v-else class="lucide-arrow-down h-3.5 w-3.5"></span>
              <span v-if="git.behind" class="font-medium">{{ git.behind }}</span>
            </button>
            <button
              class="flex items-center gap-1 rounded px-2 py-1 text-xs text-ink-gray-6 transition hover:bg-surface-gray-2 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="pushing || (!git.ahead && git.hasUpstream)" title="Push to remote" @click="push">
              <span v-if="pushing" class="lucide-loader-2 h-3.5 w-3.5 animate-spin"></span>
              <span v-else class="lucide-arrow-up h-3.5 w-3.5"></span>
              <span v-if="git.ahead" class="font-medium">{{ git.ahead }}</span>
            </button>
          </div>
        </div>
      </div>

      <div class="px-3 pb-2">
        <textarea v-model="message" rows="2" :placeholder="commitPlaceholder"
          class="w-full resize-none rounded-md border border-outline-gray-2 bg-surface-gray-2 px-2 py-2 text-base text-ink-gray-8 outline-none focus:border-outline-gray-3 sm:py-1 sm:text-sm"
          @keydown.ctrl.enter.prevent="commit" @keydown.meta.enter.prevent="commit"></textarea>
        <Button class="mt-1 w-full" variant="solid" icon-left="lucide-check" :disabled="!message.trim() || committing"
          :loading="committing" @click="commit">
          {{ commitLabel }}
        </Button>
      </div>

      <div class="min-h-0 flex-1 overflow-auto pb-24 sm:pb-2">
        <!-- staged -->
        <div v-if="git.staged.length" class="group/sec flex items-center px-3 pb-1 pt-2 sm:pt-1.5">
          <span class="text-xs uppercase tracking-wider text-ink-gray-4 sm:text-2xs">
            Staged Changes: {{ git.staged.length }}
          </span>
          <button
            class="ml-auto rounded p-1.5 text-ink-gray-5 opacity-100 transition hover:bg-surface-gray-2 sm:p-0.5 sm:opacity-0 sm:group-hover/sec:opacity-100"
            title="Unstage all" @click="unstageAll">
            <span class="lucide-minus h-4 w-4 text-current sm:h-3.5 sm:w-3.5"></span>
          </button>
        </div>
        <div class="px-1.5">
          <div v-for="f in git.staged" :key="'s' + f.path"
            class="group/row flex cursor-pointer items-center gap-1.5 rounded-md py-2 pl-1.5 pr-2 text-sm hover:bg-surface-gray-2 sm:py-1"
            :title="f.path" @click="editor.openDiff(f.path, true)">
            <FileIcon :name="baseName(f.path)" class="h-4 w-4" />
            <span class="truncate text-ink-gray-7">{{ baseName(f.path) }}</span>
            <span class="truncate text-xs text-ink-gray-4 sm:text-2xs">{{ dirName(f.path) }}</span>
            <div class="ml-auto flex items-center gap-1">
              <button
                class="rounded p-1.5 text-ink-gray-5 opacity-100 transition hover:bg-surface-gray-3 sm:p-0.5 sm:opacity-0 sm:group-hover/row:opacity-100"
                title="Unstage" @click.stop="unstage(f.path)">
                <span class="lucide-minus h-4 w-4 text-current sm:h-3.5 sm:w-3.5"></span>
              </button>
              <span class="w-3 text-center text-xs font-semibold sm:text-2xs" :class="statusColor(f.code)">{{
                letter(f.code) }}</span>
            </div>
          </div>
        </div>

        <!-- unstaged -->
        <div class="group/sec flex items-center px-3 pb-1 pt-3 sm:pt-2">
          <span class="text-xs uppercase tracking-wider text-ink-gray-4 sm:text-2xs">
            Changes: {{ git.unstaged.length }}
          </span>
          <button v-if="git.unstaged.length"
            class="ml-auto rounded p-1.5 text-ink-gray-5 opacity-100 transition hover:bg-surface-gray-2 sm:p-0.5 sm:opacity-0 sm:group-hover/sec:opacity-100"
            title="Stage all" @click="stageAll">
            <span class="lucide-plus h-4 w-4 text-current sm:h-3.5 sm:w-3.5"></span>
          </button>
        </div>
        <div class="px-1.5">
          <div v-for="f in git.unstaged" :key="'u' + f.path"
            class="group/row flex cursor-pointer items-center gap-1.5 rounded-md py-2 pl-1.5 pr-2 text-sm hover:bg-surface-gray-2 sm:py-1"
            :title="f.path" @click="editor.openDiff(f.path, false)">
            <FileIcon :name="baseName(f.path)" class="h-4 w-4" />
            <span class="truncate text-ink-gray-7">{{ baseName(f.path) }}</span>
            <span class="truncate text-xs text-ink-gray-4 sm:text-2xs">{{ dirName(f.path) }}</span>
            <div class="ml-auto flex items-center gap-1">
              <button
                class="rounded p-1.5 text-ink-gray-5 opacity-100 transition hover:bg-surface-gray-3 sm:p-0.5 sm:opacity-0 sm:group-hover/row:opacity-100"
                title="Discard changes" @click.stop="discard(f.path)">
                <span class="lucide-rotate-ccw h-4 w-4 text-current sm:h-3.5 sm:w-3.5"></span>
              </button>
              <button
                class="rounded p-1.5 text-ink-gray-5 opacity-100 transition hover:bg-surface-gray-3 sm:p-0.5 sm:opacity-0 sm:group-hover/row:opacity-100"
                title="Stage" @click.stop="stage(f.path)">
                <span class="lucide-plus h-4 w-4 text-current sm:h-3.5 sm:w-3.5"></span>
              </button>
              <span class="w-3 text-center text-xs font-semibold sm:text-2xs" :class="statusColor(f.code)">{{
                letter(f.code) }}</span>
            </div>
          </div>
          <div v-if="!git.staged.length && !git.unstaged.length" class="px-2 py-2 text-sm text-ink-gray-4 sm:text-xs">
            No changes.
          </div>
        </div>

        <!-- commit history -->
        <button
          class="flex w-full items-center gap-1 px-3 pb-1 pt-4 text-xs uppercase tracking-wider text-ink-gray-4 hover:text-ink-gray-6 sm:pt-3 sm:text-2xs"
          @click="toggleHistory"
        >
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
            class="group/row flex cursor-pointer items-start gap-2 rounded-md py-2 pl-2 pr-2 text-sm hover:bg-surface-gray-2 sm:py-1.5"
            :title="c.subject"
            @click="editor.openCommit(c.sha)"
          >
            <span class="lucide-git-commit-horizontal mt-0.5 h-4 w-4 shrink-0 text-ink-gray-4 sm:h-3.5 sm:w-3.5"></span>
            <div class="min-w-0 flex-1">
              <div class="truncate text-ink-gray-7">{{ c.subject }}</div>
              <div class="truncate text-xs text-ink-gray-4 sm:text-2xs">
                {{ c.author }} · {{ relTime(c.time) }}
              </div>
            </div>
            <code class="shrink-0 text-2xs text-ink-gray-4">{{ c.short }}</code>
          </div>
          <div v-if="!commits.length && !loadingCommits" class="px-2 py-2 text-sm text-ink-gray-4 sm:text-xs">
            No commits yet.
          </div>
          <button
            v-if="moreCommits"
            class="w-full rounded-md py-2 text-xs text-ink-gray-5 hover:bg-surface-gray-2 sm:py-1"
            :disabled="loadingCommits"
            @click="loadCommits(false)"
          >
            {{ loadingCommits ? 'Loading…' : 'Load more' }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, Dropdown, confirmDialog, toast } from 'frappe-ui'
import FileIcon from '@/components/FileIcon.vue'
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
const branchList = ref([])
const branchCurrent = ref('')
const showHistory = ref(false)
const commits = ref([])
const moreCommits = ref(false)
const loadingCommits = ref(false)

const COMMIT_PAGE = 30

const commitPlaceholder = computed(() =>
  git.staged.length ? 'Commit staged changes' : 'Commit message (stages all changes)',
)
const commitLabel = computed(() => {
  const n = git.staged.length || git.unstaged.length
  return `Commit${n ? ` (${n})` : ''}`
})

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
    if (r?.ok === false) toast.error(r.message || 'Pull failed')
    else if (showHistory.value) loadCommits()
  } finally {
    pulling.value = false
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
