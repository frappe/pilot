<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, watch } from 'vue'
import { useRouter } from 'vue-router'

import Scrollbar from '@/components/common/Scrollbar.vue'

import { useSearchIndex } from '@/components/search/index'
import type { SearchItem } from '@/components/search/index'
import { filterIndex, highlightMatch } from '@/components/search/utils'

const open = defineModel('open', { default: false })

const router = useRouter()
const query = ref('')
const activeIndex = ref(-1)
const inputRef = useTemplateRef('inputRef')
const resultsRef = useTemplateRef('resultsRef')
const searchIndex = useSearchIndex()

const filtered = computed(() => filterIndex(searchIndex.value, query.value))
const flatItems = computed(() => Object.values(filtered.value).flatMap((group) => group.items))

watch(filtered, () => {
  activeIndex.value = -1
})

watch(open, (isOpen) => {
  if (!isOpen) return
  query.value = ''
  activeIndex.value = -1
  nextTick(() => inputRef.value?.focus())
})

const close = () => {
  open.value = false
}

const select = (item: SearchItem) => {
  if (item.route) router.push(item.route)
  else item.onSelect?.()
  close()
}

const go = (index: number) => {
  const item = flatItems.value[index] ?? flatItems.value[0]
  if (item) select(item)
  else close()
}

const navigate = (delta: number) => {
  activeIndex.value = Math.min(Math.max(activeIndex.value + delta, 0), flatItems.value.length - 1)
  resultsRef.value
    ?.querySelectorAll('[role="option"]')
    [activeIndex.value]?.scrollIntoView({ block: 'nearest' })
}
</script>

<template>
  <div
    v-if="open"
    class="search-backdrop fixed inset-0 z-[100] flex items-start justify-center bg-black/70"
    @click.self="close"
  >
    <div
      class="search-panel mt-[15vh] w-full max-w-lg overflow-hidden rounded-4 bg-surface-gray-1 shadow-lg"
      @keydown.esc.prevent="close"
      @keydown.enter.prevent="go(activeIndex)"
      @keydown.up.prevent="navigate(-1)"
      @keydown.down.prevent="navigate(1)"
    >
      <div class="flex items-center gap-2 border-b border-outline-gray-2 p-3">
        <span class="lucide-search size-4 text-ink-gray-5" />
        <input
          ref="inputRef"
          v-model="query"
          placeholder="Search"
          class="w-full border-0 bg-transparent p-0 text-sm !outline-none !ring-0"
        />
        <button class="text-ink-gray-5 hover:text-ink-gray-8" aria-label="Close" @click="close">
          <span class="lucide-x size-4" />
        </button>
      </div>

      <Scrollbar v-if="flatItems.length">
        <div ref="resultsRef" class="flex max-h-[36vh] min-h-[36vh] flex-col p-2 text-sm" role="listbox">
          <template v-for="(group, name) in filtered" :key="name">
            <span class="mb-1 block px-2 py-1 text-xs uppercase text-ink-gray-4">
              {{ name }}
            </span>

            <div
              v-for="item in group.items"
              :key="`${name}-${item.name}`"
              role="option"
              class="flex cursor-pointer items-center gap-2 mb-0.5 last:mb-3 rounded-4 p-2 hover:bg-surface-gray-2"
              :class="flatItems.indexOf(item) === activeIndex ? 'bg-surface-gray-2' : ''"
              @click="select(item)"
            >
              <span :class="item.icon" class="size-4 shrink-0 text-ink-gray-6" />

              <span class="min-w-0 flex-1 truncate" v-html="highlightMatch(item.name, query)" />
            </div>
          </template>
        </div>
      </Scrollbar>

      <div v-else class="flex items-center justify-center gap-2 p-6 text-sm text-ink-gray-5">
        <span class="lucide-frown size-4" />
        No results found
      </div>

      <div class="flex items-center gap-1.5 border-t border-outline-gray-2 px-3 py-2 text-xs text-ink-gray-5">
        <kbd><span class="lucide-arrow-up size-3" /></kbd>
        <kbd class="mr-1"><span class="lucide-arrow-down size-3" /></kbd>
        <span>navigate</span>
        <kbd class="ml-3"><span class="lucide-corner-down-left size-3" /></kbd>
        <span>open</span>
        <kbd class="ml-3">esc</kbd>
        <span>close</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-backdrop {
  animation: search-backdrop-in 0.12s ease-out;
}

.search-panel {
  animation: search-panel-in 0.16s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: transform, opacity;
}

@keyframes search-backdrop-in {
  from {
    opacity: 0;
  }
}

@keyframes search-panel-in {
  from {
    opacity: 0;
    transform: translateY(6px) scale(0.98);
  }
}

:deep(mark) {
  background: var(--surface-gray-3);
  color: var(--ink-gray-9);
  font-weight: 500;
}

kbd {
  @apply inline-flex h-5 min-w-5 items-center justify-center rounded-1;
  @apply border border-outline-gray-2 bg-surface-gray-2 px-1 font-sans text-ink-gray-6;
  font-size: 0.6875rem;
  line-height: 1;
}
</style>
