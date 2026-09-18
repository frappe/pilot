<script setup lang="ts">
import SettingsGeneral from '@/components/sites/settings/General.vue'
import SettingsDomains from '@/components/sites/settings/Domains.vue'
import SettingsActions from '@/components/sites/settings/Actions.vue'
import SettingsDanger from '@/components/sites/settings/Danger.vue'

interface Props {
  siteName: string
}

defineProps<Props>()
</script>

<template>
  <SettingsGeneral :site-name="siteName" />
  <SettingsDomains :site-name="siteName" />
  <SettingsActions :site-name="siteName" />
  <SettingsDanger :site-name="siteName" />
</template>
