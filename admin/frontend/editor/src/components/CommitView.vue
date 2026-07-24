<template>
  <div class="flex h-full flex-col bg-surface-white sm:flex-row">
    <!-- files + metadata; on mobile this is the first page, hidden once a file opens -->
    <div
      v-show="!isMobile || !selectedPath"
      class="flex min-h-0 w-full flex-col sm:w-72 sm:shrink-0 sm:border-r sm:border-outline-gray-1"
    >
      <div class="flex h-11 shrink-0 items-center gap-2 border-b border-outline-gray-1 px-2 sm:h-9">
        <button class="ed-icon-button" title="Back" @click="goBack">
          <span class="lucide-chevron-left h-[18px] w-[18px] text-current sm:hidden"></span>
          <span class="lucide-x hidden h-4 w-4 text-current sm:block"></span>
        </button>
        <span class="lucide-git-commit-horizontal ed-lane text-ink-gray-5"></span>
        <span class="ed-name font-medium text-ink-gray-8">{{ info?.subject || 'Commit' }}</span>
      </div>

      <div class="min-h-0 flex-1 overflow-auto pb-24 sm:pb-2">
        <div v-if="loadingInfo" class="ed-empty">Loading…</div>

        <div v-else-if="!info" class="ed-empty">Commit unavailable.</div>

        <template v-else>
          <div class="border-b border-outline-gray-1 px-3 py-3">
            <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-gray-5">
              <span class="flex items-center gap-1">
                <span class="lucide-user h-3.5 w-3.5"></span>{{ info.author }}
              </span>
              <span class="flex items-center gap-1">
                <span class="lucide-clock h-3.5 w-3.5"></span>{{ relTime(info.time) }}
              </span>
              <button class="flex items-center gap-1 rounded hover:text-ink-gray-8" :title="info.sha" @click="copySha">
                <span class="lucide-copy h-3.5 w-3.5"></span>{{ copied ? 'Copied' : info.short }}
              </button>
            </div>
            <p v-if="info.body" class="mt-2 whitespace-pre-wrap text-sm text-ink-gray-7">{{ info.body }}</p>
          </div>

          <div class="ed-section">
            {{ info.files.length }} {{ info.files.length === 1 ? 'file changed' : 'files changed' }}
          </div>
          <div class="px-1.5">
            <div
              v-for="f in info.files"
              :key="f.path"
              class="ed-row"
              :class="{ 'ed-row-selected': !isMobile && f.path === selectedPath }"
              :title="f.path"
              @click="selectFile(f.path)"
            >
              <FileIcon :name="baseName(f.path)" class="ed-lane" />
              <span class="ed-name">{{ baseName(f.path) }}</span>
              <span class="ed-path">{{ dirName(f.path) }}</span>
              <span class="ed-lane ml-auto text-xs font-semibold" :class="statusColor(f.code)">
                {{ letter(f.code) }}
              </span>
            </div>
            <div v-if="!info.files.length" class="ed-empty">No file changes.</div>
          </div>
        </template>
      </div>
    </div>

    <!-- diff; on mobile this is the second page, shown only when a file is open -->
    <div v-show="!isMobile || selectedPath" class="flex min-h-0 w-full flex-1 flex-col">
      <div v-if="selectedPath" class="flex h-11 shrink-0 items-center gap-2 border-b border-outline-gray-1 px-2 sm:h-9">
        <button class="ed-icon-button sm:hidden" title="Back" @click="backToFiles">
          <span class="lucide-chevron-left h-[18px] w-[18px] text-current"></span>
        </button>
        <FileIcon :name="baseName(selectedPath)" class="ed-lane" />
        <span class="ed-name font-medium text-ink-gray-8">{{ baseName(selectedPath) }}</span>
        <span class="ed-path hidden sm:inline">{{ dirName(selectedPath) }}</span>
        <button class="ed-icon-button ml-auto" title="Open file" @click="openFile">
          <span class="lucide-external-link h-4 w-4 text-current"></span>
        </button>
      </div>

      <div class="min-h-0 flex-1 overflow-auto pb-24 sm:pb-2">
        <div v-if="!selectedPath" class="ed-empty hidden h-full items-center justify-center sm:flex">
          Select a file to view its changes.
        </div>
        <div v-else-if="loadingDiff" class="ed-empty">Loading…</div>
        <DiffEditor v-else :original="diff.old" :modified="diff.new" :path="selectedPath" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import FileIcon from '@/components/FileIcon.vue'
import DiffEditor from '@/components/DiffEditor.vue'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useMobile } from '@/composables/useMobile'
import { baseName, dirName, relTime } from '@/utils'

const editor = useEditorStore()
const git = useGitStore()
const { isMobile } = useMobile()

const info = ref(null)
const loadingInfo = ref(false)
const selectedPath = ref(null)
const diff = ref({ old: '', new: '' })
const loadingDiff = ref(false)
const copied = ref(false)

const sha = computed(() => editor.commit?.sha || '')

// Drill-down levels pushed onto browser history so the device/back button and
// the in-UI back buttons behave the same: diff -> file list -> close.
let pushed = 0
function pushHist() {
  history.pushState({ hatchCommit: true }, '')
  pushed++
}
function onPop() {
  if (pushed <= 0) return
  pushed--
  if (isMobile.value && selectedPath.value) selectedPath.value = null
  else editor.closeCommit()
}
function goBack() {
  if (pushed > 0) history.back()
  else editor.closeCommit()
}
function backToFiles() {
  if (pushed > 0) history.back()
  else selectedPath.value = null
}

function letter(code) {
  return code.includes('?') ? 'U' : code[0]
}
function statusColor(code) {
  return { U: 'text-ink-green-3', A: 'text-ink-green-3', D: 'text-ink-red-4' }[letter(code)] || 'text-ink-amber-3'
}

function selectFile(path) {
  if (path === selectedPath.value) return
  selectedPath.value = path
  if (isMobile.value) pushHist()
  loadDiff()
}

function openFile() {
  const p = selectedPath.value
  editor.closeCommit()
  editor.open(p).catch(() => {})
}

async function copySha() {
  try {
    await navigator.clipboard.writeText(info.value.sha)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    /* clipboard unavailable */
  }
}

async function loadDiff() {
  if (!selectedPath.value) return
  loadingDiff.value = true
  try {
    diff.value = await git.commitFileDiff(sha.value, selectedPath.value)
  } catch {
    diff.value = { old: '', new: '' }
  }
  loadingDiff.value = false
}

async function loadInfo() {
  if (!sha.value) return
  loadingInfo.value = true
  info.value = null
  selectedPath.value = null
  diff.value = { old: '', new: '' }
  try {
    info.value = await git.commitInfo(sha.value)
  } catch {
    info.value = null
  }
  loadingInfo.value = false
  // On desktop the right pane is always visible, so preselect the first file.
  if (!isMobile.value && info.value?.files?.length) {
    selectedPath.value = info.value.files[0].path
    loadDiff()
  }
}

watch(sha, loadInfo)

onMounted(() => {
  window.addEventListener('popstate', onPop)
  pushHist()
  loadInfo()
})

onBeforeUnmount(() => {
  window.removeEventListener('popstate', onPop)
  // Unwind any history entries we still own (e.g. closed via the activity bar).
  const n = pushed
  pushed = 0
  if (n > 0) history.go(-n)
})
</script>
