<template>
  <UpdatesAvailableButton />
  <p class="text-ink-gray-6">Sites page</p>
</template>

<script setup>
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import { useBreadcrumbs } from '@/composables/useBreadcrumbs'

const { setBreadcrumbs } = useBreadcrumbs()

setBreadcrumbs([{ label: 'Sites', route: { name: 'Sites' } }])
</script>
