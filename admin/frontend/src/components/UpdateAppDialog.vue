<script setup>
import { ref, watch, computed } from 'vue'
import { Button, Combobox, Dialog, LoadingText } from 'frappe-ui'
import LucideCheck from '~icons/lucide/check'

const props = defineProps({
  modelValue: Boolean,
  apps: { type: Array, default: () => [] },
  siteName: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue'])

const show = ref(props.modelValue)
watch(() => props.modelValue, v => { show.value = v })
watch(show, v => emit('update:modelValue', v))

// { [appName]: { commits: [], loading: bool, selectedCommit: '', included: bool } }
const appState = ref({})
// Track which app's combobox is open so we can expand the dialog
const openCombobox = ref(null)

watch(() => props.modelValue, async (open) => {
  if (!open || !props.apps.length) return
  openCombobox.value = null
  appState.value = Object.fromEntries(
    props.apps.map(a => [a.name, { commits: [], loading: true, selectedCommit: '', included: true }])
  )
  await Promise.all(props.apps.map(async (a) => {
    try {
      const res = await fetch(`/api/sites/${props.siteName}/apps/${a.name}/commits`)
      const d = await res.json()
      const commits = d.commits || []
      appState.value[a.name] = { commits, loading: false, selectedCommit: commits[0]?.hash || '', included: !!commits.length }
    } catch {
      appState.value[a.name] = { commits: [], loading: false, selectedCommit: '', included: false }
    }
  }))
})

const anyLoading = computed(() => Object.values(appState.value).some(s => s.loading))
const includedApps = computed(() => props.apps.filter(a => appState.value[a.name]?.included))
const allIncluded = computed(() => includedApps.value.length === props.apps.length)

function toggleAll() {
  const next = !allIncluded.value
  for (const key of Object.keys(appState.value)) {
    if (appState.value[key].commits.length) appState.value[key].included = next
  }
}

function toggleApp(name) {
  if (!appState.value[name]?.commits.length) return
  appState.value[name].included = !appState.value[name].included
}

const title = computed(() =>
  props.apps.length === 1 ? `Update ${props.apps[0].name}` : `Update ${props.apps.length} Apps`
)
</script>

<template>
  <Dialog v-model="show" :options="{ title, size: 'md' }">
    <template #body-content>
      <div class="flex flex-col" :class="openCombobox && 'pb-60'">

        <!-- Multi-app header -->
        <div v-if="apps.length > 1" class="flex items-center justify-between pb-3 mb-1">
          <span class="text-sm text-ink-gray-5">{{ includedApps.length }} of {{ apps.length }} selected</span>
          <button class="text-sm text-ink-blue-3 hover:text-ink-blue-4 transition-colors" @click="toggleAll">
            {{ allIncluded ? 'Deselect all' : 'Select all' }}
          </button>
        </div>

        <!-- App rows -->
        <div class="divide-y divide-outline-gray-1">
          <div v-for="app in apps" :key="app.name"
            class="py-3 transition-opacity"
            :class="!appState[app.name]?.included && 'opacity-40'"
          >
            <!-- App header row -->
            <div class="flex items-center justify-between"
              :class="apps.length > 1 && appState[app.name]?.commits.length && 'cursor-pointer'"
              @click="apps.length > 1 && toggleApp(app.name)"
            >
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-ink-gray-9">{{ app.name }}</span>
                <span class="font-mono text-xs text-ink-gray-4">{{ app.branch }}</span>
              </div>
              <div class="flex items-center gap-2">
                <LoadingText v-if="appState[app.name]?.loading" />
                <span v-else-if="appState[app.name]?.commits.length" class="text-xs text-ink-gray-5">
                  {{ appState[app.name].commits.length }} new commit{{ appState[app.name].commits.length > 1 ? 's' : '' }}
                </span>
                <span v-else class="text-xs text-ink-gray-4">No commits found</span>
                <LucideCheck v-if="apps.length > 1 && appState[app.name]?.included && appState[app.name]?.commits.length"
                  class="h-3.5 w-3.5 text-ink-blue-3 shrink-0" />
              </div>
            </div>

            <!-- Commits section -->
            <div v-if="appState[app.name]?.included && appState[app.name]?.commits.length" class="mt-3 flex flex-col gap-3" @click.stop>
              <!-- Combobox for commit selection -->
              <Combobox
                label="Update to commit"
                v-model="appState[app.name].selectedCommit"
                :options="appState[app.name].commits.map(c => ({
                  label: c.hash,
                  value: c.hash,
                  description: `${c.message} — ${c.author}, ${c.date}`,
                }))"
                :allowCustomValue="true"
                placeholder="Select a commit or paste a hash…"
                @update:open="openCombobox = $event ? app.name : null"
              />
              <!-- Scrollable commit list -->
              <div class="max-h-40 overflow-y-auto space-y-0.5">
                <div v-for="c in appState[app.name].commits" :key="c.hash"
                  class="flex items-start gap-3 rounded px-2 py-1.5 cursor-pointer hover:bg-surface-gray-1 transition-colors"
                  @click="appState[app.name].selectedCommit = c.hash"
                >
                  <div class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full transition-colors"
                    :class="appState[app.name].selectedCommit === c.hash ? 'bg-ink-blue-3' : 'bg-outline-gray-3'" />
                  <div class="min-w-0 flex-1">
                    <span class="font-mono text-xs text-ink-gray-5 mr-1.5">{{ c.hash }}</span>
                    <span class="text-sm text-ink-gray-8">{{ c.message }}</span>
                    <p class="mt-0.5 text-xs text-ink-gray-4">{{ c.author }} · {{ c.date }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Actions -->
        <div class="flex justify-end gap-2 pt-4 mt-2 border-t border-outline-gray-1">
          <Button variant="ghost" @click="show = false">Cancel</Button>
          <Button variant="solid" :loading="anyLoading" :disabled="!includedApps.length">
            {{ includedApps.length <= 1 ? 'Update' : `Update (${includedApps.length})` }}
          </Button>
        </div>

      </div>
    </template>
  </Dialog>
</template>
