<template>
  <Dialog v-model="open" title="Create New App" size="lg">
    <template #default>
      <div class="space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <FormControl label="App Name (snake_case)" type="text" v-model="form.name" placeholder="e.g. library_management" :autofocus="true" />
          <FormControl label="App Title" type="text" v-model="form.title" placeholder="e.g. Library Management" />
        </div>

        <FormControl label="Description" type="text" v-model="form.description" placeholder="App description" />

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <FormControl label="Publisher" type="text" v-model="form.publisher" placeholder="Publisher name" />
          <FormControl label="Email" type="text" v-model="form.email" placeholder="Publisher email" />
        </div>

        <div class="flex items-end gap-4">
          <div class="flex-1">
            <label class="text-ink-gray-7 text-xs font-medium block mb-1.5">License</label>
            <select v-model="form.app_license" class="w-full px-3 py-2 bg-surface-elevation-1 border border-outline-gray-2 rounded-lg text-ink-gray-9 text-sm focus:border-indigo-500 focus:ring-indigo-500">
              <option value="mit">MIT</option>
              <option value="gpl-3.0">GPL-3.0</option>
              <option value="agpl-3.0">AGPL-3.0</option>
              <option value="apache-2.0">Apache-2.0</option>
            </select>
          </div>
        </div>

        <div v-if="gitConnected" class="bg-surface-gray-1 p-3 border rounded-lg border-outline-gray-2 space-y-3">
          <div class="flex items-center justify-between">
            <Switch
              v-model="form.create_github_repo"
              label="GitHub Integration Connected"
              description="Create a corresponding repository on GitHub automatically."
            />
          </div>

          <div v-if="form.create_github_repo" class="flex items-center gap-2 pt-1">
            <input id="private-repo-chk" type="checkbox" v-model="form.github_repo_private" class="rounded text-indigo-600 focus:ring-indigo-500 h-4 w-4 border-gray-300">
            <label for="private-repo-chk" class="text-sm font-medium text-ink-gray-7">Private GitHub repository</label>
          </div>
        </div>

        <ErrorMessage v-if="error" :message="error" />

        <div class="flex justify-end gap-2 pt-2">
          <Button variant="subtle" @click="open = false">Cancel</Button>
          <Button variant="solid" :disabled="!canSubmit" :loading="creating" @click="submit">Create App</Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, ErrorMessage, FormControl, Switch, toast } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { appsApi } from '@/api/apps'
import { gitApi } from '@/api/git'
import { openTaskDetailPage } from '@/utils/taskRoute'

const open = defineModel('open')
const router = useRouter()

const props = defineProps({
  siteName: { type: String, default: '' }
})

const gitConnected = ref(false)
const creating = ref(false)
const error = ref('')

const form = ref({
  name: '',
  title: '',
  description: '',
  publisher: '',
  email: '',
  app_license: 'mit',
  create_github_repo: false,
  github_repo_private: false
})

watch(open, (isOpen) => {
  if (isOpen) {
    reset()
    checkGitConnection()
  }
})

// Auto-derive App Title from Name
watch(() => form.value.name, (newName) => {
  if (newName) {
    // Replace non-alphanumeric with space/underscore, capitalize words
    const title = newName
      .replace(/[^a-zA-Z0-9]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .split(' ')
      .map(w => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ')
    form.value.title = title
  } else {
    form.value.title = ''
  }
})

function reset() {
  form.value = {
    name: '',
    title: '',
    description: '',
    publisher: '',
    email: '',
    app_license: 'mit',
    create_github_repo: false,
    github_repo_private: false
  }
  error.value = ''
}

async function checkGitConnection() {
  try {
    const res = await gitApi.status()
    gitConnected.value = Boolean(res?.connected && res?.is_token_valid)
  } catch (e) {
    gitConnected.value = false
  }
}

const canSubmit = computed(() => {
  return form.value.name.trim().length > 0 && form.value.title.trim().length > 0
})

async function submit() {
  if (!canSubmit.value || creating.value) return
  creating.value = true
  error.value = ''
  
  // Basic validation: snake_case only
  const nameVal = form.value.name.trim().toLowerCase()
  if (!/^[a-z_][a-z0-9_]*$/.test(nameVal)) {
    error.value = 'App name must be in snake_case (lowercase letters, numbers, and underscores, starting with a letter or underscore).'
    creating.value = false
    return
  }

  const payload = {
    name: nameVal,
    title: form.value.title.trim(),
    description: form.value.description.trim(),
    publisher: form.value.publisher.trim(),
    email: form.value.email.trim(),
    app_license: form.value.app_license,
    create_github_repo: form.value.create_github_repo,
    github_repo_private: form.value.github_repo_private,
    sites: props.siteName ? [props.siteName] : []
  }

  try {
    const result = await appsApi.create(payload)
    if (!result.task_id) throw new Error(apiErrorMessage(result, 'Could not create app.'))
    open.value = false
    openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    error.value = caught.message || 'Could not create app.'
  } finally {
    creating.value = false
  }
}
</script>
