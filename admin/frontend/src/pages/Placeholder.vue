<script setup>
import { useRoute } from 'vue-router'
import { useI18n } from '@/i18n'

const route = useRoute()
const { t } = useI18n()
</script>

<template>
  <p class="text-ink-gray-6">{{ t(route.meta.titleKey) }}</p>
</template>
