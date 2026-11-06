<script setup lang="ts">
defineProps<{ kind: 'mail' | 'webhook' }>()

const glyphs = {
  bell: [
    'M10.268 21a2 2 0 0 0 3.464 0',
    'M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326',
  ],
  mail: [
    'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
    'm22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7',
  ],
  webhook: [
    'M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 0 1 2 17c.01-.7.2-1.4.57-2',
    'm6 17 3.13-5.78c.53-.97.1-2.18-.5-3.1a4 4 0 1 1 6.89-4.06',
    'm12 6 3.13 5.73C15.66 12.7 16.9 13 18 13a4 4 0 0 1 0 8',
  ],
}

const packets = [
  { x: 78.5, y: 27.75, opacity: 0.1 },
  { x: 100, y: 25, opacity: 0.16 },
  { x: 121.5, y: 27.75, opacity: 0.3 },
]
</script>

<template>
  <svg
    viewBox="4 6 192 62"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-44 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <path
      d="M57 36 Q100 14 143 36"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 4"
      stroke-linecap="round"
    />

    <circle
      v-for="packet in packets"
      :key="packet.x"
      :cx="packet.x"
      :cy="packet.y"
      r="2.4"
      fill="currentColor"
      :fill-opacity="packet.opacity"
    />

    <rect
      x="17"
      y="19"
      width="38"
      height="34"
      rx="10"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(36 36) scale(0.7) translate(-12 -12)"
    >
      <path v-for="path in glyphs.bell" :key="path" :d="path" />
    </g>

    <circle cx="164" cy="36" r="22" fill="currentColor" fill-opacity="0.05" />

    <circle
      cx="164"
      cy="36"
      r="16"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="2"
      stroke-dasharray="4 3"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(164 36) scale(0.62) translate(-12 -12)"
    >
      <path v-for="path in glyphs[kind]" :key="path" :d="path" />
    </g>

    <circle cx="10" cy="14" r="2" fill="currentColor" fill-opacity="0.1" />

    <path
      d="M190 54v6m-3-3h6"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-linecap="round"
    />
  </svg>
</template>
