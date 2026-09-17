<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { Button, Checkbox, Dialog, ErrorMessage, Select, TextInput } from 'frappe-ui'

import { apiErrorMessage } from '@/api/client'
import { tasksApi } from '@/api/tasks'
import { openTaskDetailPage } from '@/utils/taskRoute'

const open = defineModel('open')
const router = useRouter()

const licenseOptions = [
  'mit',
  'agpl-3.0',
  'apache-2.0',
  'bsd-2-clause',
  'bsd-3-clause',
  'bsl-1.0',
  'cc0-1.0',
  'epl-2.0',
  'gpl-2.0',
  'gpl-3.0',
  'lgpl-2.1',
  'mpl-2.0',
  'unlicense',
].map((value) => ({ label: value, value }))

const name = ref('')
const title = ref('')
const description = ref('')
const publisher = ref('')
const email = ref('')
const license = ref('mit')
const branch = ref('develop')
const githubWorkflow = ref(false)

const loading = ref(false)
const error = ref('')

const appName = computed(() =>
  name.value
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_'),
)
const titlePlaceholder = computed(() =>
  appName.value.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase()),
)

const reset = () => {
  name.value = ''
  title.value = ''
  description.value = ''
  publisher.value = ''
  email.value = ''
  license.value = 'mit'
  branch.value = 'develop'
  githubWorkflow.value = false
  error.value = ''
}

watch(open, (isOpen) => {
  if (isOpen) reset()
})

const submit = async () => {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await tasksApi.run('new-app', {
      name: appName.value,
      title: title.value.trim(),
      description: description.value.trim(),
      publisher: publisher.value.trim(),
      email: email.value.trim(),
      license: license.value,
      branch: branch.value.trim(),
      github_workflow: githubWorkflow.value,
    })
    if (!result.task_id) throw new Error(apiErrorMessage(result, 'Could not create the app.'))
    open.value = false
    openTaskDetailPage(router, result.task_id)
  } catch (caught) {
    error.value = caught.message || 'Could not create the app.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <Dialog v-model="open" title="Create new app" size="md">
    <form class="flex flex-col gap-3" @submit.prevent="submit">
      <TextInput
        label="App name"
        v-model="name"
        placeholder="people_ops"
        pattern="[A-Za-z][A-Za-z0-9_ -]*"
        required
      />

      <TextInput label="Title" v-model="title" :placeholder="titlePlaceholder || 'People Ops'" />

      <TextInput
        label="Description"
        v-model="description"
        placeholder="What this app does"
        required
      />

      <div class="flex gap-2">
        <TextInput label="Publisher" v-model="publisher" class="flex-1" required />
        <TextInput label="Email" type="email" v-model="email" class="flex-1" required />
      </div>

      <div class="flex gap-2">
        <Select label="License" v-model="license" :options="licenseOptions" class="flex-1" />
        <TextInput label="Initial branch" v-model="branch" class="flex-1" />
      </div>

      <Checkbox v-model="githubWorkflow" label="Add a GitHub Actions unittest workflow" />

      <ErrorMessage v-if="error" :message="error" />

      <p v-else-if="appName && appName !== name.trim()" class="text-ink-gray-5 text-sm">
        Will be created as {{ appName }}
      </p>
      <div class="flex justify-end gap-2 mt-2">
        <Button @click="open = false">Cancel</Button>
        <Button type="submit" variant="solid" :loading="loading">Create app</Button>
      </div>
    </form>
  </Dialog>
</template>

<style scoped>
form:invalid button[type='submit'] {
  @apply opacity-50 pointer-events-none;
}
</style>
