<script setup lang="ts">
defineProps<{
  kind: 'two-factor' | 'ssh-keys' | 'ip-rules' | 'waf-rules' | 'sessions' | 'activity'
}>()

const ring = 'M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0'

const glyphs = {
  keyRound: [
    'M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z',
    'M16.5 7.5h.01',
  ],
  ban: [ring, 'm4.9 4.9 14.2 14.2'],
  plus: ['M12 5v14', 'M5 12h14'],
  listFilter: ['M3 6h18', 'M7 12h10', 'M10 18h4'],
  fileText: [
    'M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z',
    'M14 2v4a2 2 0 0 0 2 2h4',
    'M10 9H8',
    'M16 13H8',
    'M16 17H8',
  ],
  history: ['M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 'M3 3v5h5', 'M12 7v5l4 2'],
  pointer: [
    'M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z',
  ],
}

const twoFactorPackets = [
  { x: 68.4, y: 40.3, opacity: 0.1 },
  { x: 81.3, y: 45.5, opacity: 0.16 },
  { x: 94.5, y: 50.7, opacity: 0.3 },
]

const codeBoxes = [21, 29.5, 38]
const passwordDots = [121, 128, 135, 142]
const ipSegments = [50, 64, 78, 92]

const activityPackets = [
  { x: 92, y: 26.6, opacity: 0.1 },
  { x: 110, y: 24.5, opacity: 0.16 },
  { x: 128, y: 27.6, opacity: 0.3 },
]
</script>

<template>
  <svg
    v-if="kind === 'two-factor'"
    viewBox="8 4 184 68"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-44 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <path
      d="M52 38 C78 38 84 53 112 53"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 4"
      stroke-linecap="round"
    />

    <circle
      v-for="packet in twoFactorPackets"
      :key="packet.x"
      :cx="packet.x"
      :cy="packet.y"
      r="2.2"
      fill="currentColor"
      :fill-opacity="packet.opacity"
    />

    <rect
      x="16"
      y="10"
      width="34"
      height="56"
      rx="8"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 3"
    />

    <rect x="28" y="15" width="10" height="2.5" rx="1.25" fill="currentColor" fill-opacity="0.12" />

    <rect
      v-for="(box, index) in codeBoxes"
      :key="box"
      :x="box"
      y="33.5"
      width="7"
      height="9"
      rx="2"
      fill="currentColor"
      :fill-opacity="index ? 0.1 : 0.2"
    />

    <rect
      x="106"
      y="12"
      width="80"
      height="54"
      rx="9"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
    />

    <path
      d="M106 21a9 9 0 0 1 9-9h62a9 9 0 0 1 9 9v3H106z"
      fill="currentColor"
      fill-opacity="0.05"
    />

    <path d="M107 24h78" stroke="currentColor" stroke-opacity="0.08" />
    <circle cx="114" cy="18" r="1.6" fill="currentColor" fill-opacity="0.2" />
    <circle cx="120" cy="18" r="1.6" fill="currentColor" fill-opacity="0.12" />
    <circle cx="126" cy="18" r="1.6" fill="currentColor" fill-opacity="0.08" />

    <rect
      x="114"
      y="31"
      width="64"
      height="12"
      rx="4"
      stroke="currentColor"
      stroke-opacity="0.16"
    />

    <circle
      v-for="dot in passwordDots"
      :key="dot"
      :cx="dot"
      cy="37"
      r="1.8"
      fill="currentColor"
      fill-opacity="0.3"
    />

    <rect
      x="114"
      y="47"
      width="64"
      height="12"
      rx="4"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-dasharray="3 3"
    />

    <path
      d="M180 66v6m-3-3h6"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-linecap="round"
    />
  </svg>

  <svg
    v-else-if="kind === 'ssh-keys'"
    viewBox="40 0 128 86"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-36 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <ellipse cx="100" cy="76" rx="52" ry="8" fill="currentColor" fill-opacity="0.04" />
    <rect x="74" y="6" width="76" height="52" rx="9" fill="currentColor" fill-opacity="0.04" />

    <rect
      x="64"
      y="14"
      width="76"
      height="52"
      rx="9"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.12"
    />

    <rect
      x="54"
      y="22"
      width="76"
      height="52"
      rx="9"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
    />

    <path d="M54 31a9 9 0 0 1 9-9h58a9 9 0 0 1 9 9v3H54z" fill="currentColor" fill-opacity="0.05" />
    <path d="M55 34h74" stroke="currentColor" stroke-opacity="0.08" />
    <circle cx="62" cy="28" r="1.8" fill="currentColor" fill-opacity="0.2" />
    <circle cx="68" cy="28" r="1.8" fill="currentColor" fill-opacity="0.12" />
    <circle cx="74" cy="28" r="1.8" fill="currentColor" fill-opacity="0.08" />

    <path
      d="m62 42 4 3-4 3"
      stroke="currentColor"
      stroke-opacity="0.3"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />

    <rect x="70" y="43" width="38" height="4" rx="2" fill="currentColor" fill-opacity="0.16" />

    <path
      d="m62 53 4 3-4 3"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />

    <rect x="70" y="54" width="24" height="4" rx="2" fill="currentColor" fill-opacity="0.1" />
    <rect x="62" y="63" width="5" height="6" rx="1" fill="currentColor" fill-opacity="0.25" />
    <circle cx="140" cy="66" r="15" fill="currentColor" fill-opacity="0.05" />

    <circle
      cx="140"
      cy="66"
      r="11"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="3 3"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(140 66) scale(0.48) translate(-12 -12)"
    >
      <path v-for="path in glyphs.keyRound" :key="path" :d="path" />
    </g>

    <circle cx="48" cy="18" r="2" fill="currentColor" fill-opacity="0.1" />
  </svg>

  <svg
    v-else-if="kind === 'ip-rules'"
    viewBox="12 4 176 72"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-44 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <rect
      x="20"
      y="10"
      width="160"
      height="26"
      rx="8"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.16"
    />

    <circle cx="35" cy="23" r="8" fill="currentColor" fill-opacity="0.08" />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(35 23) scale(0.4) translate(-12 -12)"
    >
      <path v-for="path in glyphs.ban" :key="path" :d="path" />
    </g>

    <rect
      v-for="segment in ipSegments"
      :key="segment"
      :x="segment"
      y="20.5"
      width="10"
      height="5"
      rx="2.5"
      fill="currentColor"
      fill-opacity="0.16"
    />

    <circle
      v-for="segment in ipSegments.slice(1)"
      :key="segment"
      :cx="segment - 2"
      cy="25"
      r="1"
      fill="currentColor"
      fill-opacity="0.3"
    />

    <rect x="108" y="19" width="18" height="8" rx="4" fill="currentColor" fill-opacity="0.08" />
    <rect x="160" y="20.5" width="12" height="5" rx="2.5" fill="currentColor" fill-opacity="0.08" />

    <rect
      x="20"
      y="44"
      width="160"
      height="26"
      rx="8"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-dasharray="4 3"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.3"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(100 57) scale(0.5) translate(-12 -12)"
    >
      <path v-for="path in glyphs.plus" :key="path" :d="path" />
    </g>

    <circle cx="16" cy="66" r="2" fill="currentColor" fill-opacity="0.1" />
  </svg>

  <svg
    v-else-if="kind === 'waf-rules'"
    viewBox="8 4 180 68"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-44 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <path
      d="M58 38H81"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 4"
      stroke-linecap="round"
    />

    <path
      d="M119 38C135 38 138 16 154 16M119 38C135 38 138 60 154 60"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 4"
      stroke-linecap="round"
    />

    <rect
      x="14"
      y="24"
      width="44"
      height="28"
      rx="8"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
    />

    <rect x="21" y="31" width="12" height="5" rx="2.5" fill="currentColor" fill-opacity="0.25" />
    <rect x="36" y="31" width="15" height="5" rx="2.5" fill="currentColor" fill-opacity="0.12" />
    <rect x="21" y="40" width="24" height="4" rx="2" fill="currentColor" fill-opacity="0.1" />

    <rect
      x="81"
      y="21"
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
      transform="translate(100 38) scale(0.7) translate(-12 -12)"
    >
      <path v-for="path in glyphs.listFilter" :key="path" :d="path" />
    </g>

    <circle
      cx="166"
      cy="16"
      r="11"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="3 3"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(166 16) scale(0.45) translate(-12 -12)"
    >
      <path v-for="path in glyphs.ban" :key="path" :d="path" />
    </g>

    <circle
      cx="166"
      cy="60"
      r="11"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="3 3"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="translate(166 60) scale(0.45) translate(-12 -12)"
    >
      <path v-for="path in glyphs.fileText" :key="path" :d="path" />
    </g>

    <circle cx="16" cy="12" r="2" fill="currentColor" fill-opacity="0.1" />
  </svg>

  <svg
    v-else-if="kind === 'sessions'"
    viewBox="24 4 140 76"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-36 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <ellipse cx="94" cy="75" rx="56" ry="4" fill="currentColor" fill-opacity="0.04" />

    <rect
      x="40"
      y="10"
      width="88"
      height="54"
      rx="6"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 3"
    />

    <circle cx="58" cy="30" r="7" fill="currentColor" fill-opacity="0.08" />
    <rect x="70" y="25" width="38" height="4" rx="2" fill="currentColor" fill-opacity="0.12" />
    <rect x="70" y="33" width="24" height="4" rx="2" fill="currentColor" fill-opacity="0.08" />

    <rect
      x="30"
      y="64"
      width="108"
      height="6"
      rx="3"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 3"
    />

    <rect
      x="122"
      y="30"
      width="28"
      height="44"
      rx="6"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 3"
    />

    <rect
      x="131"
      y="34.5"
      width="10"
      height="2.5"
      rx="1.25"
      fill="currentColor"
      fill-opacity="0.12"
    />

    <circle cx="136" cy="49" r="5" fill="currentColor" fill-opacity="0.08" />

    <rect
      x="129"
      y="58"
      width="14"
      height="3.5"
      rx="1.75"
      fill="currentColor"
      fill-opacity="0.08"
    />

    <circle cx="30" cy="12" r="2" fill="currentColor" fill-opacity="0.1" />

    <path
      d="M154 10v6m-3-3h6"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-linecap="round"
    />
  </svg>

  <svg
    v-else
    viewBox="4 8 192 58"
    fill="none"
    aria-hidden="true"
    focusable="false"
    class="h-auto w-44 max-w-full text-ink-gray-9 pointer-events-none select-none"
  >
    <path
      d="M74 34 Q110 14 146 36"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
      stroke-dasharray="4 4"
      stroke-linecap="round"
    />

    <circle
      v-for="packet in activityPackets"
      :key="packet.x"
      :cx="packet.x"
      :cy="packet.y"
      r="2.4"
      fill="currentColor"
      :fill-opacity="packet.opacity"
    />

    <rect
      x="14"
      y="24"
      width="58"
      height="22"
      rx="7"
      class="fill-surface-elevation-1"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-width="1.5"
    />

    <rect x="22" y="33" width="22" height="4" rx="2" fill="currentColor" fill-opacity="0.2" />

    <path
      d="M48.5 36.5 46 34M52 35.5V32M48 40h-3.5"
      stroke="currentColor"
      stroke-opacity="0.25"
      stroke-width="1.5"
      stroke-linecap="round"
    />

    <g
      stroke="currentColor"
      stroke-opacity="0.4"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      class="fill-surface-elevation-1"
      transform="translate(58 46) scale(0.75) translate(-12 -12)"
    >
      <path v-for="path in glyphs.pointer" :key="path" :d="path" />
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
      <path v-for="path in glyphs.history" :key="path" :d="path" />
    </g>

    <circle cx="10" cy="16" r="2" fill="currentColor" fill-opacity="0.1" />
  </svg>
</template>
