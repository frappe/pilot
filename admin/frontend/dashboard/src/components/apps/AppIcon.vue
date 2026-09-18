<script setup lang="ts">
import { computed } from 'vue'
import { Avatar } from 'frappe-ui'

import {
  FRAPPE_LOGO_URL,
  hashTheme,
  isFrappeFramework,
  useAppRegistry,
} from '@/composables/apps/useAppRegistry'

interface Props {
  name: string
  label?: string
  logo?: string
  size?: string
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  logo: '',
  size: '2xl',
})

const { logoMap } = useAppRegistry()

const logoUrl = computed(() => {
  if (isFrappeFramework(props.name)) return FRAPPE_LOGO_URL
  return props.logo || logoMap.value[props.name]
})
</script>

<template>
  <Avatar
    class="[&_img]:object-contain"
    :image="logoUrl"
    :label="label || name"
    :size="size"
    :theme="hashTheme(name)"
    shape="square"
  />
</template>
