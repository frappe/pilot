<script setup lang="ts">
import { ErrorMessage } from 'frappe-ui'
import { onMounted, ref } from 'vue'

import ConfigEditor from '@/components/common/ConfigEditor.vue'

import { settingsApi } from '@/api/settings'
import { errorMessage } from '@/utils/error'

const config = ref<Record<string, unknown>>({})
const managedKeys = ref<string[]>([])
const error = ref('')

const reload = async () => {
  try {
    config.value = await settingsApi.commonSiteConfig.get()
    error.value = ''
  } catch (e) {
    error.value = errorMessage(e, 'Could not load common site config.')
  }
}

onMounted(async () => {
  await reload()
  managedKeys.value = await settingsApi.commonSiteConfig.managedKeys().catch(() => [])
})
</script>

<template>
  <ErrorMessage v-if="error" :message="error" class="mb-3" />
  <ConfigEditor
    :config="config"
    title="Common site config"
    :hidden-keys="managedKeys"
    file-name="sites/common_site_config.json"
    :save="settingsApi.commonSiteConfig.update"
    :reload="reload"
  />
</template>
