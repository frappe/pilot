<script setup lang="ts">
import { Dialog, FormControl } from 'frappe-ui'
import { usePrompt } from '@/composables/usePrompt'

const { state, submit, cancel } = usePrompt()
</script>

<template>
  <Dialog
    :open="state.visible"
    :title="state.title"
    :actions="[
      { label: 'Cancel', onClick: cancel },
      { label: 'OK', variant: 'solid', onClick: submit },
    ]"
    @update:open="(v: boolean) => !v && cancel()"
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
