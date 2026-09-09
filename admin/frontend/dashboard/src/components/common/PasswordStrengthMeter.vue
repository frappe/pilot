<script setup lang="ts">
import { computed } from 'vue'

import { PASSWORD_REQUIREMENTS } from '@/utils/passwordStrength'

interface Props {
  password?: string
}

const props = withDefaults(defineProps<Props>(), {
  password: '',
})

const requirements = computed(() =>
  PASSWORD_REQUIREMENTS.map((req) => ({ label: req.label, met: req.test(props.password) })),
)
</script>

<template>
  <ul v-if="password" class="flex flex-col gap-0.5">
    <li
      v-for="req in requirements"
      :key="req.label"
      class="flex items-center gap-1.5 text-xs"
      :class="req.met ? 'text-ink-green-5' : 'text-ink-gray-4'"
    >
      <span class="size-3 lucide-check" />
      {{ req.label }}
    </li>
  </ul>
</template>
