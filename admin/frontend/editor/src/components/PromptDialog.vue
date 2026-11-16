<template>
  <Dialog
    :open="state.visible"
    :title="state.title"
    :actions="[
      { label: 'Cancel', onClick: cancel },
      { label: 'OK', variant: 'solid', onClick: submit },
    ]"
    @update:open="(v) => !v && cancel()"
  >
    <FormControl
      type="text"
      :placeholder="state.placeholder"
      v-model="state.value"
      autofocus
      @keydown.enter="submit"
    />
  </Dialog>
</template>

<script setup>
import { Dialog, FormControl } from 'frappe-ui'
import { usePrompt } from '@/composables/usePrompt'

const { state, submit, cancel } = usePrompt()
</script>
