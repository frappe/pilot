<template>
  <div class="mt-6">
    <div class="flex sm:flex-row flex-col gap-2">
      <FormControl
        v-model="searchModel"
        class="flex-1"
        type="text"
        placeholder="Search for any app"
      >
        <template #prefix>
          <LucideSearch class="size-4 text-ink-gray-5" />
        </template>
      </FormControl>

      <div class="flex gap-2">
        <Dropdown :options="worksWithMenu" placement="bottom-end">
          <template #default="{ open }">
            <Button class="w-32 [&>.truncate]:flex-1 [&>.truncate]:text-left" :active="open">
              <template #suffix><span class="size-4 shrink-0 lucide-chevron-down" /></template>
              {{ worksWithLabel }}
            </Button>
          </template>
        </Dropdown>

        <Button variant="subtle" @click="$emit('add-from-github')">
          <template #prefix><GithubMark class="size-4" /></template>
          Import app
        </Button>

        <Button variant="solid" @click="$emit('create-app')">
          <template #prefix><span class="size-4 lucide-plus" /></template>
          Create app
        </Button>
      </div>
    </div>

    <div class="flex flex-wrap gap-1.5 mt-3">
      <button
        v-for="pill in PILLS"
        :key="pill"
        type="button"
        class="px-3 py-0.5 border rounded-full text-p-sm transition duration-150 ease-[var(--ease-out)] active:scale-[0.97]"
        :class="pill === pillModel
          ? 'bg-surface-gray-3 border-outline-gray-2 text-ink-gray-9'
          : 'border-outline-gray-2 text-ink-gray-6 hover:bg-surface-gray-1 hover:text-ink-gray-8'"
        @click="pillModel = pill"
      >
        {{ pill }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, h } from 'vue'
import { Button, Dropdown, FormControl } from 'frappe-ui'
import LucideSearch from '~icons/lucide/search'
import GithubMark from '@/components/icons/GithubMark.vue'
import { PILLS } from '@/utils/marketplaceCategories'

const props = defineProps({
  worksWithOptions: { type: Array, default: () => [] },
})
defineEmits(['add-from-github', 'create-app'])

const searchModel = defineModel('search', { type: String })
const pillModel = defineModel('pill', { type: String })
const worksWithModel = defineModel('worksWith', { type: String })

function appLogo(option) {
  if (!option.logo_url) return null
  return () => h('img', { src: option.logo_url, class: 'size-4 rounded object-contain' })
}

const worksWithMenu = computed(() => [
  {
    label: 'Any app',
    icon: () => h('span', { class: 'size-4 text-ink-gray-6 lucide-layout-grid' }),
    onClick: () => (worksWithModel.value = ''),
  },
  ...props.worksWithOptions.map((option) => ({
    label: option.title,
    icon: appLogo(option),
    onClick: () => (worksWithModel.value = option.name),
  })),
])

const worksWithLabel = computed(() => {
  const selected = props.worksWithOptions.find((option) => option.name === worksWithModel.value)
  return selected ? `Works with ${selected.title}` : 'Works with'
})
</script>
