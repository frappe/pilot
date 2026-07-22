<template>
  <div
    class="bg-chrome hidden shrink-0 flex-col items-center gap-1 border-r border-outline-gray-1 py-2 sm:flex sm:w-11"
  >
    <button
      v-for="it in items"
      :key="it.id"
      class="relative flex h-9 w-9 items-center justify-center rounded-md"
      :class="{
        'text-ink-gray-9': active === it.id,
        'text-ink-gray-5 hover:text-ink-gray-8': active !== it.id,
      }"
      :title="it.title"
      @click="$emit('select', it.id)"
    >
      <span
        v-if="active === it.id"
        class="absolute bottom-1.5 left-0 top-1.5 w-0.5 rounded-full bg-surface-gray-7"
      ></span>
      <span :class="it.icon" class="h-[18px] w-[18px] text-current"></span>
      <span
        v-if="it.id === 'git' && gitCount"
        class="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-surface-gray-7 px-1 text-2xs font-medium text-ink-white"
      >
        {{ gitCount }}
      </span>
    </button>
  </div>
</template>

<script setup>
defineProps({ active: String, gitCount: { type: Number, default: 0 } })
defineEmits(['select'])

const items = [
  { id: 'files', icon: 'lucide-file', title: 'Explorer', label: 'Files' },
  { id: 'search', icon: 'lucide-search', title: 'Search', label: 'Search' },
  { id: 'git', icon: 'lucide-git-branch', title: 'Source Control', label: 'Git' },
]
</script>
