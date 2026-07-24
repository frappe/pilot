<template>
  <div>
    <!-- dim backdrop while the dial is open -->
    <transition name="fade">
      <div
        v-if="open"
        class="fixed inset-0 z-40 bg-black/40"
        @click="open = false"
      ></div>
    </transition>

    <!-- editor controls float at the top, clear of the corner flower -->
    <transition name="drop">
      <div
        v-if="open && hasFile"
        class="fixed left-1/2 top-3 z-50 flex -translate-x-1/2 items-center gap-2"
      >
        <div
          class="flex items-center rounded-full bg-panel py-1 pl-3 pr-1 shadow-lg ring-1 ring-outline-gray-2"
        >
          <span class="lucide-type mr-1 h-3.5 w-3.5 text-ink-gray-4"></span>
          <button
            class="flex h-8 w-8 items-center justify-center rounded-full text-ink-gray-6 hover:bg-surface-gray-2"
            aria-label="Decrease font size"
            @click="$emit('font', -1)"
          >
            <span class="lucide-minus h-4 w-4 text-current"></span>
          </button>
          <span class="w-7 text-center text-sm font-semibold tabular-nums text-ink-gray-8">{{ fontSize }}</span>
          <button
            class="flex h-8 w-8 items-center justify-center rounded-full text-ink-gray-6 hover:bg-surface-gray-2"
            aria-label="Increase font size"
            @click="$emit('font', 1)"
          >
            <span class="lucide-plus h-4 w-4 text-current"></span>
          </button>
        </div>
        <button
          class="flex h-11 w-11 items-center justify-center rounded-full shadow-lg ring-1 ring-outline-gray-2"
          :class="editable ? 'fab-solid' : 'bg-panel text-ink-gray-7'"
          :aria-label="editable ? 'Switch to read-only' : 'Enable editing'"
          @click="$emit('toggle-edit')"
        >
          <span :class="editable ? 'lucide-pencil' : 'lucide-lock'" class="h-[18px] w-[18px] text-current"></span>
        </button>
      </div>
    </transition>

    <!-- corner flower -->
    <div
      class="fixed right-4 z-50"
      :style="{ bottom: 'calc(1rem + env(safe-area-inset-bottom, 0px))' }"
    >
      <button
        v-for="(it, i) in items"
        :key="it.id"
        class="petal absolute bottom-1 right-1 flex h-12 w-12 items-center justify-center rounded-full shadow-lg ring-1 ring-outline-gray-2"
        :class="active === it.id ? 'fab-solid' : 'bg-panel text-ink-gray-7'"
        :style="petalStyle(i)"
        :aria-label="it.label"
        :title="it.label"
        @click="choose(it)"
      >
        <span :class="it.icon" class="h-5 w-5 text-current"></span>
        <span
          v-if="it.id === 'git' && gitCount"
          class="fab-solid absolute -right-0.5 -top-0.5 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-2xs font-semibold ring-2 ring-panel"
        >
          {{ gitCount }}
        </span>
      </button>

      <button
        class="fab-solid relative flex h-14 w-14 items-center justify-center rounded-full shadow-xl transition-transform active:scale-95"
        :aria-label="open ? 'Close menu' : 'Open menu'"
        @click="open = !open"
      >
        <span
          class="h-6 w-6 text-current transition-transform duration-200"
          :class="[open ? 'lucide-x' : mainIcon, { 'rotate-90': open }]"
        ></span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  active: String,
  gitCount: { type: Number, default: 0 },
  hasFile: { type: Boolean, default: false },
  editable: { type: Boolean, default: false },
  fontSize: { type: Number, default: 13 },
})
const emit = defineEmits(['select', 'goto', 'toggle-edit', 'font'])

const open = ref(false)

const items = [
  { id: 'files', icon: 'lucide-folder', label: 'Explorer', kind: 'panel' },
  { id: 'search', icon: 'lucide-search', label: 'Search', kind: 'panel' },
  { id: 'git', icon: 'lucide-git-branch', label: 'Source Control', kind: 'panel' },
  { id: 'goto', icon: 'lucide-file-text', label: 'Go to File', kind: 'command' },
]

const R = 164

// Bloom along a quarter arc from straight-up to straight-left (upper-left
// quadrant), so the petals fan out like a flower from the corner FAB.
function petalStyle(i) {
  const n = items.length
  const deg = 90 + (n === 1 ? 45 : (i * 90) / (n - 1))
  const rad = (deg * Math.PI) / 180
  const dx = Math.cos(rad) * R
  const dy = -Math.sin(rad) * R
  if (open.value) {
    return {
      transform: `translate(${dx}px, ${dy}px)`,
      opacity: 1,
      transitionDelay: `${i * 35}ms`,
      pointerEvents: 'auto',
    }
  }
  return {
    transform: 'translate(0, 0) scale(0.3)',
    opacity: 0,
    transitionDelay: `${(n - 1 - i) * 25}ms`,
    pointerEvents: 'none',
  }
}

const mainIcon = computed(() => {
  const a = items.find((it) => it.id === props.active)
  return a ? a.icon : 'lucide-grid'
})

function choose(it) {
  if (it.kind === 'panel') emit('select', it.id)
  else emit(it.id)
  open.value = false
}
</script>

<style scoped>
.petal {
  transition:
    transform 0.28s cubic-bezier(0.34, 1.4, 0.64, 1),
    opacity 0.2s ease;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.drop-enter-active,
.drop-leave-active {
  transition:
    transform 0.22s ease,
    opacity 0.2s ease;
}
.drop-enter-from,
.drop-leave-to {
  opacity: 0;
  transform: translate(-50%, -12px);
}
</style>
