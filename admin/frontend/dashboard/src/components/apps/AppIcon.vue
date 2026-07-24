<template>
  <div
    class="grid size-10 shrink-0 place-items-center overflow-hidden rounded-lg"
    :style="logoUrl ? {} : { background: hashColor(name) }"
  >
    <img
      v-if="logoUrl"
      :src="logoUrl"
      :alt="name"
      class="size-full object-contain"
      @error="onError"
    />
    <span v-else class="text-sm font-bold text-white">
      {{ initial }}
    </span>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import {
  FRAPPE_LOGO_URL,
  isFrappeFramework,
  useAppRegistry,
} from '@/composables/apps/useAppRegistry'

const props = defineProps({
  name: { type: String, required: true },
})

const { logoMap, hashColor } = useAppRegistry()
const hasError = ref(false)

const isFrappe = computed(() => isFrappeFramework(props.name))

const logoUrl = computed(() => {
  if (isFrappe.value) return FRAPPE_LOGO_URL
  return hasError.value ? null : logoMap.value[props.name]
})
const initial = computed(() => props.name[0]?.toUpperCase() || '')

function onError() {
  if (!isFrappe.value) hasError.value = true
}
</script>
