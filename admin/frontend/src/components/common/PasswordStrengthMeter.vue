<template>
  <ul v-if="password" class="flex flex-col gap-0.5">
    <li
      v-for="req in requirements"
      :key="req.label"
      class="flex items-center gap-1.5 text-xs"
      :class="req.met ? 'text-ink-green-6' : 'text-ink-gray-4'"
    >
      <LucideCheck class="size-3" />
      {{ req.label }}
    </li>
  </ul>
</template>

<script setup>
import { computed } from 'vue'
import LucideCheck from '~icons/lucide/check'
import { PASSWORD_REQUIREMENTS } from '../../utils/passwordStrength'

const props = defineProps({
  password: { type: String, default: '' },
})

const requirements = computed(() =>
  PASSWORD_REQUIREMENTS.map((req) => ({ label: req.label, met: req.test(props.password) })),
)
</script>
