<template>
  <div class="flex h-full flex-col bg-surface-white">
    <!-- header -->
    <div class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-3 py-2 sm:py-1.5">
      <FileIcon :name="baseName(path)" class="h-4 w-4 shrink-0" />
      <span class="truncate text-sm font-medium text-ink-gray-8">{{ baseName(path) }}</span>
      <span class="truncate text-2xs text-ink-gray-4">{{ dirName(path) }}</span>

      <div v-if="showSwitcher" class="ml-2 flex rounded-md border border-outline-gray-2 text-2xs">
          <button
            class="rounded-l-md px-2 py-1 text-sm sm:py-0.5 sm:text-xs"
            :class="side === 'unstaged' ? 'bg-surface-gray-3 text-ink-gray-9' : 'text-ink-gray-5'"
            @click="setSide('unstaged')"
          >
            Unstaged
          </button>
          <button
            class="rounded-r-md px-2 py-1 text-sm sm:py-0.5 sm:text-xs"
            :class="side === 'staged' ? 'bg-surface-gray-3 text-ink-gray-9' : 'text-ink-gray-5'"
            @click="setSide('staged')"
          >
            Staged
          </button>
      </div>

      <div class="ml-auto flex items-center gap-1">
        <button
          class="rounded p-2 text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8 sm:p-1"
          title="Open file"
          @click="openFile"
        >
          <span class="lucide-external-link h-5 w-5 text-current sm:h-3.5 sm:w-3.5"></span>
        </button>
        <button
          class="rounded p-2 text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8 sm:p-1"
          title="Close diff"
          @click="editor.closeDiff()"
        >
          <span class="lucide-x h-5 w-5 text-current sm:h-4 sm:w-4"></span>
        </button>
      </div>
    </div>

    <div ref="body" class="min-h-0 flex-1 overflow-auto" @mouseup="onMouseUp">
      <div v-if="loading" class="p-4 text-xs text-ink-gray-4">loading…</div>

      <div v-else-if="!view.length" class="p-4 text-xs text-ink-gray-4">
        {{ untracked ? 'Empty new file.' : 'No ' + side + ' changes for this file.' }}
      </div>

      <!-- hunks -->
      <template v-else>
        <div v-for="(h, hi) in view" :key="hi">
          <div class="bg-surface-gray-1 px-3 py-1.5 font-mono text-2xs text-ink-gray-5 sm:py-1">
            {{ h.label }}
          </div>
          <div class="font-mono leading-[1.5]" :style="{ fontSize: fontSize + 'px' }">
            <div
              v-for="row in h.rows"
              :key="row.key"
              :data-key="row.key"
              class="flex py-1 sm:py-0"
              :class="[rowBg(row), { 'cursor-pointer': row.selectable }]"
              @click="row.selectable && onLineClick(row, $event)"
              @contextmenu="onLineMenu(row, $event)"
              @touchstart="row.selectable && onLineTouchStart(row, $event)"
              @touchend="onLineTouchEnd"
              @touchmove="onLineTouchMove"
            >
              <span class="w-9 shrink-0 select-none pr-2 text-right tabular-nums text-ink-gray-4 sm:w-10" :style="numStyle">{{ row.oldNo }}</span>
              <span class="w-9 shrink-0 select-none pr-2 text-right tabular-nums text-ink-gray-4 sm:w-10" :style="numStyle">{{ row.newNo }}</span>
              <span class="w-3 shrink-0 select-none text-center" :class="signColor(row.type)">{{ row.sign }}</span>
              <span class="whitespace-pre text-ink-gray-8">{{ row.content }}</span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useMobile } from '@/composables/useMobile'
import FileIcon from '@/components/FileIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useGitStore } from '@/stores/git'
import { useContextMenu } from '@/composables/useContextMenu'
import { parseDiff, buildPatch } from '@/lib/gitdiff'
import { baseName, dirName } from '@/utils'

const editor = useEditorStore()
const git = useGitStore()
const { open: openMenu } = useContextMenu()
const { isMobile } = useMobile()

const side = ref('unstaged')
const loading = ref(false)
const untracked = ref(false)
const parsed = ref({ header: '', hunks: [] })
const selected = ref(new Set())
const body = ref(null)
let anchor = null
const longPressTimer = ref(null)
let longPressTriggered = false

const path = computed(() => editor.diff?.path || '')

// Match Monaco's font size so the diff reads at the same scale as the code.
const fontSize = computed(() => editor.fontSize)
const numStyle = computed(() => ({ fontSize: Math.max(9, fontSize.value - 2) + 'px' }))

// The Unstaged/Staged toggle only makes sense for a file that has changes on
// both sides (partially staged). Otherwise we just show what the user clicked.
const showSwitcher = computed(
  () =>
    git.staged.some((f) => f.path === path.value) &&
    git.unstaged.some((f) => f.path === path.value),
)

const view = computed(() => {
  return parsed.value.hunks.map((h, hi) => {
    let oln = h.oldStart
    let nln = h.newStart
    const rows = []
    h.lines.forEach((ln, li) => {
      const key = hi + ':' + li
      if (ln.type === '\\') {
        rows.push({ key, sign: '', type: '\\', oldNo: '', newNo: '', content: ln.text, selectable: false })
        return
      }
      const text = ln.text.slice(1)
      if (ln.type === ' ') {
        rows.push({ key, sign: ' ', type: ' ', oldNo: oln++, newNo: nln++, content: text, selectable: false })
      } else if (ln.type === '-') {
        rows.push({ key, sign: '-', type: '-', oldNo: oln++, newNo: '', content: text, selectable: true })
      } else {
        rows.push({ key, sign: '+', type: '+', oldNo: '', newNo: nln++, content: text, selectable: true })
      }
    })
    return { label: '@@ -' + h.oldStart + ' +' + h.newStart + ' @@' + (h.context || ''), rows }
  })
})

// Ordered list of selectable keys, for shift-range selection.
const order = computed(() =>
  view.value.flatMap((h) => h.rows.filter((r) => r.selectable).map((r) => r.key)),
)

function rowBg(row) {
  if (selected.value.has(row.key)) return 'bg-surface-amber-1'
  if (row.type === '+') return 'bg-surface-green-1'
  if (row.type === '-') return 'bg-surface-red-1'
  return ''
}
function signColor(t) {
  if (t === '+') return 'text-ink-green-3'
  if (t === '-') return 'text-ink-red-4'
  return 'text-ink-gray-4'
}

function nearestKeyNode(node) {
  while (node && node !== body.value) {
    if (node.hasAttribute?.('data-key')) return node
    node = node.parentNode
  }
  return null
}

function coveredKeys(range) {
  const set = new Set()
  if (!body.value) return set
  const selectable = order.value
  const start = nearestKeyNode(range.startContainer)
  const end = nearestKeyNode(range.endContainer)
  if (!start && !end) return set

  const allRows = body.value.querySelectorAll('[data-key]')
  if (!start) {
    for (const el of allRows) {
      const k = el.getAttribute('data-key')
      if (k === end.getAttribute('data-key')) break
      if (selectable.includes(k) && range.intersectsNode(el)) set.add(k)
    }
    if (selectable.includes(end.getAttribute('data-key')) && range.intersectsNode(end)) set.add(end.getAttribute('data-key'))
    return set
  }
  if (!end) {
    for (let i = allRows.length - 1; i >= 0; i--) {
      const el = allRows[i]
      const k = el.getAttribute('data-key')
      if (k === start.getAttribute('data-key')) break
      if (selectable.includes(k) && range.intersectsNode(el)) set.add(k)
    }
    if (selectable.includes(start.getAttribute('data-key')) && range.intersectsNode(start)) set.add(start.getAttribute('data-key'))
    return set
  }

  let inRange = false
  for (const el of allRows) {
    const k = el.getAttribute('data-key')
    if (k === start.getAttribute('data-key')) inRange = true
    if (inRange) {
      if (selectable.includes(k) && range.intersectsNode(el)) set.add(k)
    }
    if (k === end.getAttribute('data-key')) break
  }
  return set
}

function onMouseUp() {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !sel.rangeCount) return
  const keys = coveredKeys(sel.getRangeAt(0))
  if (keys.size) {
    selected.value = keys
    anchor = null
  }
}

function onLineTouchStart(row, e) {
  clearTimeout(longPressTimer.value)
  longPressTriggered = false
  longPressTimer.value = setTimeout(() => {
    longPressTriggered = true
    onLineMenu(row, e.touches[0] || e)
  }, 500)
}
function onLineTouchMove() {
  clearTimeout(longPressTimer.value)
}
function onLineTouchEnd() {
  clearTimeout(longPressTimer.value)
}

function onLineClick(row, e) {
  // A click that ends a text-drag shouldn't collapse the line selection.
  if (longPressTriggered) {
    longPressTriggered = false
    return
  }
  const winSel = window.getSelection()
  if (winSel && !winSel.isCollapsed) return
  const s = new Set(selected.value)
  if (e.shiftKey && anchor != null) {
    const a = order.value.indexOf(anchor)
    const b = order.value.indexOf(row.key)
    if (a !== -1 && b !== -1) {
      const [lo, hi] = a < b ? [a, b] : [b, a]
      s.clear()
      for (let i = lo; i <= hi; i++) s.add(order.value[i])
    }
  } else if (e.metaKey || e.ctrlKey) {
    s.has(row.key) ? s.delete(row.key) : s.add(row.key)
    anchor = row.key
  } else if (s.has(row.key)) {
    s.delete(row.key)
    anchor = null
  } else {
    s.clear()
    s.add(row.key)
    anchor = row.key
  }
  selected.value = s
}

function onLineMenu(row, e) {
  // Decide what to act on: a text selection wins; otherwise the clicked line
  // (unless it's already part of the current selection).
  const winSel = window.getSelection()
  if (winSel && !winSel.isCollapsed && winSel.rangeCount) {
    const keys = coveredKeys(winSel.getRangeAt(0))
    if (keys.size) selected.value = keys
  } else if (row.selectable && !selected.value.has(row.key)) {
    selected.value = new Set([row.key])
    anchor = row.key
  }
  if (!selected.value.size) {
    e.preventDefault()
    return
  }
  const n = selected.value.size
  const s = n > 1 ? 's' : ''
  let items
  if (untracked.value) {
    items = [
      { label: `Stage ${n} line${s}`, icon: 'lucide-plus', action: () => applySelected('stage') },
      { label: 'Discard file', icon: 'lucide-rotate-ccw', danger: true, action: discardFile },
    ]
  } else if (side.value === 'unstaged') {
    items = [
      { label: `Stage ${n} line${s}`, icon: 'lucide-plus', action: () => applySelected('stage') },
      { label: `Discard ${n} line${s}`, icon: 'lucide-rotate-ccw', danger: true, action: () => applySelected('discard') },
    ]
  } else {
    items = [{ label: `Unstage ${n} line${s}`, icon: 'lucide-minus', action: () => applySelected('unstage') }]
  }
  openMenu(e, items)
}

async function load() {
  if (!path.value) return
  loading.value = true
  untracked.value = false
  selected.value = new Set()
  anchor = null
  const d = await git.fileDiff(path.value, side.value === 'staged')
  untracked.value = !!d.untracked
  parsed.value = parseDiff(d.diff || '')
  loading.value = false
}

function setSide(s) {
  if (side.value === s) return
  side.value = s
  load()
}

watch(
  () => [editor.diff?.path, editor.diff?.staged],
  () => {
    if (!editor.diff) return
    side.value = editor.diff.staged ? 'staged' : 'unstaged'
    load()
  },
  { immediate: true },
)

function openFile() {
  const p = path.value
  editor.closeDiff()
  editor.open(p).catch(() => {})
}

async function discardFile() {
  if (!confirm(`Delete ${baseName(path.value)}? This removes the untracked file.`)) return
  const r = await git.discard(path.value)
  if (r?.ok === false) {
    alert(r.message || 'Discard failed')
    return
  }
  editor.closeDiff()
}

async function applySelected(kind) {
  if (!selected.value.size) return
  if (kind === 'discard' && !confirm(`Discard ${selected.value.size} selected line(s)?`)) return
  const reverse = kind !== 'stage'
  const cached = kind !== 'discard'
  const patch = buildPatch(parsed.value, selected.value, reverse)
  if (!patch) return
  const r = await git.apply(patch, cached, reverse)
  if (r?.ok === false) {
    alert(r.message || 'git apply failed')
    return
  }
  await git.refresh()
  if (editor.tabs.find((t) => t.path === path.value)) {
    await editor.refreshTab(path.value).catch(() => {})
  }
  await load()
}
</script>
