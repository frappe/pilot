<script setup lang="ts">
import {
  Alert,
  Button,
  MobileNavItem,
  Popover,
  Select,
  SidebarItem,
  Spinner,
  TabButtons,
} from 'frappe-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import EmptyState from '@/components/common/EmptyState.vue'
import Scrollbar from '@/components/common/Scrollbar.vue'

import { useNotifications } from '@/composables/notifications/useNotifications'
import type { Notification } from '@/types/notification'
import { relativeTime } from '@/utils/time'

interface SeverityLook {
  icon: string
  text: string
  bg: string
}

interface Props {
  mobile?: boolean
}

withDefaults(defineProps<Props>(), {
  mobile: false,
})

const badgePollMs = 60000

const tabs = [
  { label: 'All', value: 'all' },
  { label: 'Unread', value: 'unread' },
]

const categories = [
  { label: 'All categories', value: '', icon: 'lucide-layers' },
  { label: 'Sites', value: 'Sites', icon: 'lucide-globe' },
  { label: 'Tasks', value: 'Tasks', icon: 'lucide-list-checks' },
  { label: 'Server', value: 'Server', icon: 'lucide-server' },
  { label: 'Updates', value: 'Updates', icon: 'lucide-git-pull-request-arrow' },
]

const severityLook: Record<string, SeverityLook> = {
  Error: { icon: 'lucide-circle-alert', text: 'text-ink-red-8', bg: 'bg-surface-red-1' },
  Warning: { icon: 'lucide-triangle-alert', text: 'text-ink-amber-8', bg: 'bg-surface-amber-1' },
  Info: { icon: 'lucide-info', text: 'text-ink-blue-8', bg: 'bg-surface-blue-1' },
}

const {
  notifications,
  unread,
  loading,
  loadingMore,
  error,
  hasMore,
  load,
  loadMore,
  refreshBadge,
  markAsRead,
  markAllAsRead,
} = useNotifications()

const router = useRouter()
const isOpen = ref(false)
const activeTab = ref('all')
const category = ref('')

const badge = computed(() => {
  if (!unread.value) return ''

  return unread.value > 99 ? '99+' : String(unread.value)
})

const filters = computed(() => ({
  category: category.value,
  unreadOnly: activeTab.value === 'unread',
}))

const look = (severity: string): SeverityLook => severityLook[severity] || severityLook.Info

const onRowClick = async (item: Notification) => {
  await markAsRead(item.name)

  if (!item.action_route) return

  isOpen.value = false
  router.push(item.action_route)
}

watch([isOpen, filters], ([opened]) => {
  if (opened) load(filters.value)
})

const badgeTimer = setInterval(refreshBadge, badgePollMs)

refreshBadge()
onUnmounted(() => clearInterval(badgeTimer))
</script>

<template>
  <Popover
    v-model:open="isOpen"
    bare
    :side="mobile ? 'top' : 'right'"
    :offset="mobile ? 0 : 9"
    :collision-padding="0"
  >
    <template #trigger>
      <MobileNavItem v-if="mobile" label="Notifications">
        <span class="relative block size-6">
          <span class="lucide-bell block size-6 text-ink-gray-5" />
          <span
            v-if="unread > 0"
            class="top-0 right-0 absolute bg-surface-blue-6 rounded-full size-1.5 shrink-0"
          />
        </span>
      </MobileNavItem>

      <SidebarItem v-else label="Notifications" :suffix="badge" class="mb-3 text-sm">
        <template #prefix>
          <span class="relative block size-4">
            <span class="lucide-bell block size-4" />
            <span
              v-if="unread > 0"
              class="top-0 right-[1px] absolute bg-surface-blue-6 rounded-full size-[5px] shrink-0"
            />
          </span>
        </template>
      </SidebarItem>
    </template>

    <aside
      class="flex flex-col bg-surface-base md:border-r border-outline-gray-1 w-screen md:w-[430px] h-[calc(100dvh-3.5rem)] md:h-screen"
    >
      <header class="flex items-center gap-1 py-2 pr-2 pl-4 border-outline-gray-1 border-b">
        <span class="mr-auto font-medium">Notifications</span>
        <Button
          v-if="unread > 0"
          variant="ghost"
          icon="lucide-check-check"
          aria-label="Mark all as read"
          @click="markAllAsRead"
        />
        <Button
          variant="ghost"
          icon="lucide-x"
          aria-label="Close notifications"
          @click="isOpen = false"
        />
      </header>

      <div class="flex flex-none items-center gap-2 px-4 py-3">
        <TabButtons v-model="activeTab" :options="tabs" />
        <Select v-model="category" class="ml-auto" :options="categories" />
      </div>

      <Alert v-if="error" class="border border-outline-gray-2" theme="red" title="Couldn't load notifications" :dismissible="false">
        <template #description>{{ error }}</template>
      </Alert>

      <Scrollbar v-else-if="notifications.length" class="min-h-0">
        <button
          v-for="(item, index) in notifications"
          :key="item.name"
          type="button"
          class="flex items-start gap-4 hover:bg-surface-gray-1 p-4 w-full text-left cursor-pointer"
          :class="index === notifications.length - 1 ? '' : 'border-b border-outline-gray-1'"
          @click="onRowClick(item)"
        >
          <span
            class="relative place-items-center grid mt-0.5 rounded-4 size-8 shrink-0"
            :class="look(item.severity).bg"
          >
            <span :class="[look(item.severity).icon, look(item.severity).text, 'size-4']" />
            <span
              v-if="!item.is_read"
              class="-top-px -right-px absolute bg-surface-blue-5 rounded-full size-1.5 shrink-0"
            />
          </span>

          <span class="flex-1 min-w-0">
            <span class="flex items-start gap-2">
              <span class="flex-1 min-w-0 font-medium">
                {{ item.title }}
              </span>

              <span class="text-ink-gray-5 text-xs whitespace-nowrap shrink-0">
                {{ relativeTime(item.created_at) }}
              </span>
            </span>

            <span v-if="item.message" class="block mt-0.5 text-ink-gray-6 text-p-sm">
              {{ item.message }}
            </span>
          </span>
        </button>
      </Scrollbar>

      <div v-else-if="loading" class="flex flex-1 justify-center items-center">
        <Spinner size="lg" class="text-ink-gray-4" />
      </div>

      <div v-else class="flex-1 px-4 pb-3 min-h-0">
        <EmptyState
          :icon="activeTab === 'unread' ? 'lucide-check-check' : 'lucide-bell-off'"
          :title="activeTab === 'unread' ? `You're all caught up` : 'Nothing here yet'"
          :description="
            activeTab === 'unread'
              ? 'Every notification on this bench has been read.'
              : 'Failed tasks and resource alerts for this bench show up here.'
          "
        />
      </div>

      <footer v-if="hasMore" class="flex justify-end mt-auto p-2 border-outline-gray-1 border-t">
        <Button label="Load more" :loading="loadingMore" @click="loadMore(filters)" />
      </footer>
    </aside>
  </Popover>
</template>
