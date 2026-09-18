<script setup lang="ts">
import { ref } from 'vue'
import { TextInput } from 'frappe-ui'

interface Props {
  label?: string
  placeholder?: string
  autocomplete?: string
}

withDefaults(defineProps<Props>(), {
  label: '',
  placeholder: '',
  autocomplete: 'off',
})

const password = defineModel({ type: String, default: '' })
const visible = ref(false)
</script>

<template>
  <TextInput
    v-model="password"
    :label="label"
    :type="visible ? 'text' : 'password'"
    :placeholder="placeholder"
    :autocomplete="autocomplete"
  >
    <template #suffix>
      <button
        type="button"
        tabindex="-1"
        class="text-ink-gray-5 hover:text-ink-gray-7"
        :aria-label="visible ? 'Hide password' : 'Show password'"
        @click="visible = !visible"
      >
        <span v-if="visible" class="size-4 lucide-eye-off" />
        <span v-else class="size-4 lucide-eye" />
      </button>
    </template>
  </TextInput>
</template>
