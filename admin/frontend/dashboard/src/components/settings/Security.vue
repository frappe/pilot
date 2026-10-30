<script setup lang="ts">
import { TabButtons } from 'frappe-ui'
import { computed } from 'vue'
import Account from '@/components/settings/Account.vue'
import Firewall from '@/components/settings/Firewall.vue'
import SshKeys from '@/components/settings/SshKeys.vue'
import { SECURITY_SECTIONS } from '@/components/settings/sections'
import Waf from '@/components/settings/Waf.vue'

const openSection = defineModel<{ id: string } | null>('openSection')

const currentTab = computed(() => openSection.value?.id ?? SECURITY_SECTIONS[0].id)

const tabs = SECURITY_SECTIONS.map((section) => ({
  value: section.id,
  label: section.label,
  iconLeft: section.icon,
}))

const selectTab = (id: string | number) => {
  openSection.value = SECURITY_SECTIONS.find((section) => section.id === id) ?? null
}
</script>

<template>
  <TabButtons
    :model-value="currentTab"
    :options="tabs"
    class="self-start mb-4"
    @update:model-value="selectTab"
  />

  <Account v-if="currentTab === 'account'" />
  <SshKeys v-else-if="currentTab === 'ssh-keys'" />
  <Firewall v-else-if="currentTab === 'ip-rules'" />
  <Waf v-else />
</template>
