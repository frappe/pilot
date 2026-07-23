<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <p class="text-ink-gray-6">{{ route.meta.title }} page</p>
</template>
