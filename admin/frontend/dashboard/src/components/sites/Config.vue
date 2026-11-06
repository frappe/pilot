<script setup lang="ts">
import ConfigEditor from '@/components/common/ConfigEditor.vue'

import { sitesApi } from '@/api/sites'
import { useSite } from '@/composables/sites/useSite'

const props = defineProps<{ siteName: string }>()

const { site, reload } = useSite(props.siteName)
</script>

<template>
  <ConfigEditor
    :config="site?.site_config || {}"
    file-name="site_config.json"
    description="Keys passed to this site's"
    :save="(patch) => sitesApi.configuration.update(props.siteName, patch)"
    :reload="reload"
  />
</template>
