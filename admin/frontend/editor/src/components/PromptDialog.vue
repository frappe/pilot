<template>
  <Dialog
    :modelValue="state.visible"
    @update:modelValue="(v) => !v && cancel()"
    :options="{ title: state.title }"
  >
    <template #body-content>
      <FormControl
        type="text"
        :placeholder="state.placeholder"
        v-model="state.value"
        autofocus
        @keydown.enter="submit"
      />
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" @click="cancel" />
        <Button variant="solid" label="OK" @click="submit" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { Dialog, Button, FormControl } from 'frappe-ui'
import { usePrompt } from '@/composables/usePrompt'

const { state, submit, cancel } = usePrompt()
</script>
