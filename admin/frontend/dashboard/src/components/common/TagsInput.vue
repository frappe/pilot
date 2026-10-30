<script setup lang="ts">
import {
  TagsInputInput,
  TagsInputItem,
  TagsInputItemDelete,
  TagsInputItemText,
  TagsInputRoot,
} from 'reka-ui'

const props = withDefaults(
  defineProps<{
    placeholder?: string
    variant?: 'subtle' | 'outline'
    isInvalid?: (tag: string) => boolean
  }>(),
  {
    placeholder: '',
    variant: 'subtle',
    isInvalid: () => false,
  },
)

const fieldClasses = {
  subtle:
    'bg-surface-gray-2 focus-within:bg-surface-base border-[--surface-gray-2] hover:border-outline-elevation-2',
  outline: 'bg-surface-base border-outline-gray-2 hover:border-outline-gray-3 hover:shadow-sm',
}

const tagClasses = {
  subtle: 'bg-surface-gray-3 text-ink-gray-7',
  outline: 'bg-surface-gray-2 text-ink-gray-7 hover:bg-surface-gray-3',
}

const model = defineModel<string[]>({ required: true })
</script>

<template>
  <TagsInputRoot
    v-model="model"
    add-on-blur
    add-on-paste
    class="flex flex-wrap items-center gap-1 p-1 border focus-within:border-outline-gray-4 rounded-4 focus-within:shadow-sm min-h-7 text-base transition-colors cursor-text"
    :class="fieldClasses[props.variant]"
  >
    <TagsInputItem
      v-for="tag in model"
      :key="tag"
      :value="tag"
      class="tag flex items-center gap-0.5 data-[state=active]:outline data-[state=active]:outline-2 data-[state=active]:[outline-color:var(--outline-gray-3)] pr-1 pl-1.5 rounded-2 h-5 text-xs"
      :class="
        isInvalid(tag) ? 'bg-surface-red-2 text-ink-red-7' : tagClasses[props.variant]
      "
    >
      <TagsInputItemText />

      <TagsInputItemDelete
        class="flex justify-center items-center bg-transparent p-0 border-0 size-3 shrink-0"
        :class="
          isInvalid(tag)
            ? 'text-ink-red-5 hover:text-ink-red-7'
            : 'text-ink-gray-5 hover:text-ink-gray-8'
        "
      >
        <span class="size-3 lucide-x" aria-hidden="true" />
      </TagsInputItemDelete>
    </TagsInputItem>

    <TagsInputInput
      :placeholder="placeholder"
      class="flex-1 bg-transparent p-0 px-1.5 border-0 focus:ring-0 min-w-28 h-5 text-ink-gray-8 placeholder:text-ink-gray-4 text-sm"
    />
  </TagsInputRoot>
</template>

<style scoped>
@media (prefers-reduced-motion: no-preference) {
  .tag {
    animation: tag-in 140ms ease-out;
  }
}

@keyframes tag-in {
  from {
    opacity: 0;
    scale: 0.92;
  }
}
</style>
