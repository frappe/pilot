<template>
  <Dialog v-model="open" bare size="3xl">
    <template #default>
      <div class="flex h-[39rem] max-h-[85vh]">
        <div class="flex flex-col p-3 border-r border-outline-gray-2 w-52 shrink-0">
          <h3 class="p-2 font-semibold text-ink-gray-9 text-base">Settings</h3>
          <div class="flex flex-col gap-0.5">
            <button v-for="section in sections" :key="section.id" type="button"
              class="flex items-center gap-2 px-2.5 py-1.5 rounded text-p-sm text-left transition-colors" :class="activeSection === section.id
                ? 'bg-surface-gray-3 text-ink-gray-9'
                : 'text-ink-gray-6 hover:bg-surface-gray-2'" @click="activeSection = section.id">
              <span :class="section.icon" class="size-4"></span>
              {{ section.label }}
            </button>
          </div>
        </div>
        <div class="flex-1 p-6 overflow-y-auto">
          <h3 class="pb-4 font-semibold text-ink-gray-9 text-lg">{{ activeSectionLabel }}</h3>
          <Workers v-if="activeSection === 'workers'" />
          <Firewall v-else-if="activeSection === 'firewall'" />
          <SystemInfo v-else-if="activeSection === 'system-info'" />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Dialog } from 'frappe-ui'
import Firewall from '@/components/settings/Firewall.vue'
import SystemInfo from '@/components/settings/SystemInfo.vue'
import Workers from '@/components/settings/Workers.vue'

const open = defineModel()

const sections = [
  { id: 'workers', label: 'Workers', icon: 'lucide-server' },
  { id: 'firewall', label: 'Firewall', icon: 'lucide-shield' },
  { id: 'system-info', label: 'System Info', icon: 'lucide-info' },
]
const activeSection = ref(sections[0].id)
const activeSectionLabel = computed(() => sections.find((s) => s.id === activeSection.value)?.label)
</script>
