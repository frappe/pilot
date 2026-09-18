<script setup lang="ts">
interface Props {
  icon?: string
}

withDefaults(defineProps<Props>(), {
  icon: '',
})
</script>

<template>
  <div
    class="relative px-3 sm:px-4 pt-3 sm:pt-4 pb-2 overflow-hidden"
  >
    <div class="absolute inset-0 pointer-events-none dot-field" aria-hidden="true" />
    <div
      class="relative flex justify-between items-center gap-3 mx-auto mt-2 bg-surface-base p-2 sm:p-4 border rounded-7 w-full max-w-3xl border-outline-gray-2"
    >
      <div class="flex items-center gap-3 min-w-0">
        <slot name="icon">
          <span
            v-if="icon"
            class="place-items-center grid bg-surface-gray-2 rounded-6 size-9 sm:size-10 text-ink-gray-6 shrink-0"
          >
            <span class="size-4 sm:size-5" :class="icon" />
          </span>
        </slot>

        <div class="min-w-0">
          <div class="flex items-center gap-2 min-w-0">
            <slot name="title" />
          </div>

          <div
            v-if="$slots.subtitle"
            class="hidden sm:flex items-center mt-1 text-ink-gray-5 text-sm"
          >
            <slot name="subtitle" />
          </div>
        </div>
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.dot-field {
  background-image: radial-gradient(var(--outline-gray-3) 1.1px, transparent 1.3px);
  background-size: 12px 12px;
  background-position: -5px -5px;
  mask-image: linear-gradient(to bottom, rgb(0 0 0 / 0.95), transparent 100%);
}
</style>
