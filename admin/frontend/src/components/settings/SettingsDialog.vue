<template>
  <Dialog v-model="open" bare size="3xl">
    <template #default="{ close }">
      <div class="relative flex sm:h-[70vh] max-h-[85vh]">
        <div class="flex-col p-4 sm:border-r border-outline-gray-2 w-full sm:w-52 shrink-0"
          :class="activeSection ? 'hidden sm:flex' : 'flex'">
          <h3
            class="mb-1 p-2 pb-3 border-b sm:border-b-0 border-outline-gray-2 font-semibold text-ink-gray-9 text-base">
            Settings</h3>
          <Button v-if="!activeSection" class="sm:hidden top-3 right-3 absolute" variant="ghost" icon="lucide-x"
            @click="close" />
          <div class="flex flex-col gap-2 sm:gap-0.5 pt-2 sm:pt-0">
            <Button v-for="section in sections" :key="section.id" :variant="isMobile ? 'subtle' : 'ghost'"
              :size="isMobile ? 'md' : 'sm'" class="!justify-start border sm:border-0 w-full"
              :class="currentSection === section.id ? 'sm:!bg-surface-gray-3 sm:!text-ink-gray-9 !text-ink-gray-6' : '!text-ink-gray-6'"
              @click="activeSection = section.id">
              <template #prefix>
                <span :class="section.icon" class="size-4"></span>
              </template>
              {{ section.label }}
            </Button>
          </div>
        </div>
        <div class="flex-col flex-1 p-6 overflow-y-auto" :class="activeSection ? 'flex' : 'hidden sm:flex'">
          <div class="flex justify-between items-center pb-4">
            <div class="flex items-center gap-2">
              <Button class="sm:hidden -ml-2" variant="subtle" icon="lucide-arrow-left" @click="activeSection = null" />
              <h3 class="font-semibold text-ink-gray-9 text-lg">{{ activeSectionLabel }}</h3>
            </div>
            <div id="settings-header-actions" class="contents"></div>
            <Button v-if="currentSection === 'workers'" variant="subtle" icon-left="lucide-plus"
              @click="workersRef?.addGroup()">Add</Button>
            <Button v-else-if="currentSection === 'ssh-keys'" variant="subtle" icon-left="lucide-plus"
              @click="sshKeysRef?.openAdd()">Add</Button>
          </div>
          <General v-if="currentSection === 'general'" />
          <Workers v-else-if="currentSection === 'workers'" ref="workersRef" />
          <Firewall v-else-if="currentSection === 'firewall'" />
          <Waf v-else-if="currentSection === 'waf'" />
          <Git v-else-if="currentSection === 'github'" />
          <S3Bucket v-else-if="currentSection === 's3-bucket'" />
          <LLM v-else-if="currentSection === 'llm'" />
          <SshKeys v-else-if="currentSection === 'ssh-keys'" ref="sshKeysRef" />
          <SystemInfo v-else-if="currentSection === 'system-info'" />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Dialog, Button } from 'frappe-ui'
import General from '@/components/settings/General.vue'
import Firewall from '@/components/settings/Firewall.vue'
import Waf from '@/components/settings/Waf.vue'
import Git from '@/components/settings/Git.vue'
import S3Bucket from '@/components/settings/S3Bucket.vue'
import LLM from '@/components/settings/LLM.vue'
import SshKeys from '@/components/settings/SshKeys.vue'
import SystemInfo from '@/components/settings/SystemInfo.vue'
import Workers from '@/components/settings/Workers.vue'
import { useIsMobile } from '@/composables/common/useIsMobile'

const open = defineModel()

const isMobile = useIsMobile()

const sections = computed(() => [
  { id: 'general', label: 'General', icon: 'lucide-settings' },
  { id: 'github', label: 'Git', icon: 'lucide-git-branch' },
  { id: 'workers', label: 'Workers', icon: 'lucide-server-cog' },
  { id: 's3-bucket', label: 'Object Storage', icon: 'lucide-archive' },
  { id: 'llm', label: 'AI Assistant', icon: 'lucide-sparkles' },
  { id: 'firewall', label: 'Firewall', icon: 'lucide-shield' },
  { id: 'waf', label: 'WAF', icon: 'lucide-shield-alert' },
  { id: 'ssh-keys', label: 'SSH Keys', icon: 'lucide-key-round' },
  { id: 'system-info', label: 'System Info', icon: 'lucide-info' },
])
const activeSection = ref(null)
const workersRef = ref(null)
const sshKeysRef = ref(null)
const currentSection = computed(() => activeSection.value ?? sections.value[0].id)
const activeSectionLabel = computed(() => sections.value.find((s) => s.id === currentSection.value)?.label)
</script>
