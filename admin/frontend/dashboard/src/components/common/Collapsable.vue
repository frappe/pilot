<script setup lang="ts">
interface Props {
  headerCss?: string
  disabled?: boolean
}

const props = defineProps<Props>()

const opened = defineModel<boolean>('opened', { default: false })

const toggle = () => {
  if (props.disabled) return
  opened.value = !opened.value
}
</script>

<template>
  <slot name="header" v-bind="{ opened, toggle }">
    <div
      class="flex items-center gap-2"
      :class="[disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer', headerCss]"
      :aria-expanded="opened"
      @click="toggle"
    >
      <slot name="prefix" />
      <span
        class="shrink-0 size-4 ml-auto transition-transform duration-300 lucide-chevron-up"
        :class="opened ? '' : 'rotate-180'"
      />
    </div>
  </slot>

  <div
    :inert="!opened"
    class="grid transition-[grid-template-rows] duration-300"
    :class="opened ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'"
  >
    <div class="overflow-hidden"><slot /></div>
  </div>
</template>
