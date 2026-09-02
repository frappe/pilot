<script setup lang="ts">
interface Props {
  app: string
  message: string
}

defineProps<Props>()

const docsUrl = 'https://github.com/frappe/pilot/blob/develop/docs/app-processes.md'
</script>

<template>
  <div class="bg-surface-red-1 p-4 border border-outline-red-2 rounded-7">
    <div class="flex items-start gap-3">
      <span class="mt-0.5 size-4 text-ink-red-4 lucide-triangle-alert shrink-0" />

      <div class="min-w-0">
        <p class="font-medium text-ink-red-4 text-base">
          Processes cannot start while {{ app }} is misconfigured
        </p>
        <p class="mt-1 text-ink-gray-7 text-p-sm leading-5">
          The bench builds every process from its apps, so one bad declaration stops the whole
          list. Nothing was changed on the server.
        </p>

        <pre
          class="bg-surface-red-2 mt-3 p-3 rounded-6 overflow-x-auto font-mono text-ink-red-4 text-xs leading-5 whitespace-pre-wrap"
        >{{ message }}</pre>

        <a
          :href="docsUrl"
          target="_blank"
          rel="noreferrer"
          class="inline-flex items-center gap-1.5 mt-3 text-ink-gray-7 hover:text-ink-gray-8 text-p-sm underline"
        >
          How app processes are declared
          <span class="size-3.5 lucide-external-link" />
        </a>
      </div>
    </div>
  </div>
</template>
