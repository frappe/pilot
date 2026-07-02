<template>
  <Dialog v-model="open" bare size="3xl">
    <template #default="{ close }">
      <div class="relative flex sm:h-[39rem] min-h-[24rem] max-h-[85vh]">
        <div class="flex-col p-4 sm:border-r border-outline-gray-2 w-full sm:w-52 shrink-0"
          :class="activeSection ? 'hidden sm:flex' : 'flex'">
          <h3
            class="mb-1 p-2 pb-3 border-b sm:border-b-0 border-outline-gray-2 font-semibold text-ink-gray-9 text-base">
            Settings</h3>
          <Button v-if="!activeSection" class="sm:hidden top-3 right-3 absolute" variant="ghost" icon="lucide-x"
            @click="close" />
          <div class="flex flex-col gap-0.5">
            <Button v-for="section in sections" :key="section.id" variant="ghost" class="!justify-start w-full"
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
            <Button v-if="currentSection === 'workers'" variant="subtle" icon-left="lucide-plus"
              @click="workersRef?.addGroup()">Add</Button>
          </div>
          <Workers v-if="currentSection === 'workers'" ref="workersRef" />
          <Firewall v-else-if="currentSection === 'firewall'" />
          <Git v-else-if="currentSection === 'github'" />
          <S3Bucket v-else-if="currentSection === 's3-bucket'" />
          <Snapshots v-else-if="currentSection === 'snapshots'" />
          <SystemInfo v-else-if="currentSection === 'system-info'" />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { Dialog, Button } from 'frappe-ui'
import Firewall from '@/components/settings/Firewall.vue'
import Git from '@/components/settings/Git.vue'
import S3Bucket from '@/components/settings/S3Bucket.vue'
import Snapshots from '@/components/settings/Snapshots.vue'
import SystemInfo from '@/components/settings/SystemInfo.vue'
import Workers from '@/components/settings/Workers.vue'
import { settingsApi } from '@/api/settings'

const open = defineModel()

const zfsEnabled = ref(false)

const sections = computed(() => [
  { id: 'github', label: 'Git Settings', icon: 'lucide-git-branch' },
  { id: 's3-bucket', label: 'S3 Bucket', icon: 'lucide-archive' },
  { id: 'workers', label: 'Workers', icon: 'lucide-server-cog' },
  { id: 'firewall', label: 'Firewall', icon: 'lucide-shield' },
  ...(zfsEnabled.value ? [{ id: 'snapshots', label: 'Snapshots', icon: 'lucide-camera' }] : []),
  { id: 'system-info', label: 'System Info', icon: 'lucide-info' },
])
const activeSection = ref(null)
const workersRef = ref(null)
const currentSection = computed(() => activeSection.value ?? sections.value[0].id)
const activeSectionLabel = computed(() => sections.value.find((s) => s.id === currentSection.value)?.label)

async function loadZfsEnabled() {
  try {
    const data = await settingsApi.get()
    zfsEnabled.value = !!data.volume?.enabled
  } catch {
    zfsEnabled.value = false
  }
}

watch(open, (value) => { if (value) loadZfsEnabled() })
onMounted(() => { if (open.value) loadZfsEnabled() })
</script>
