<script setup lang="ts">
defineProps<{ step: string }>()
</script>

<template>
  <svg viewBox="0 0 320 420" class="setup-art w-full h-auto">
    <g v-if="step === 'github'">
      <g class="spin" style="transform-origin: 160px 196px">
        <circle cx="160" cy="196" r="108" fill="none" stroke-dasharray="2 6" class="stroke-ink-gray-3" />
        <circle cx="160" cy="196" r="152" fill="none" stroke-dasharray="2 6" class="stroke-ink-gray-3" />
        <circle cx="268" cy="196" r="2.5" class="fill-ink-gray-3" />
        <circle cx="52" cy="196" r="2.5" class="fill-ink-gray-3" />
      </g>

      <path d="M192 172 L236 128" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />
      <path d="M196 214 L240 252" fill="none" stroke-dasharray="3 5" class="stroke-ink-gray-3" />
      <path d="M126 216 L84 262" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />
      <path d="M128 174 L88 136" fill="none" stroke-dasharray="3 5" class="stroke-ink-gray-3" />

      <g
        v-for="repo in [
          { x: 226, y: 96 },
          { x: 230, y: 240 },
          { x: 18, y: 258 },
          { x: 20, y: 104 },
        ]"
        :key="`${repo.x}-${repo.y}`"
        :transform="`translate(${repo.x} ${repo.y})`"
      >
        <rect width="76" height="40" rx="8" class="fill-surface-elevation-1 stroke-ink-gray-3" />

        <rect x="8" y="9" width="22" height="22" rx="5" class="fill-surface-gray-2" />
        <g
          fill="none"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
          transform="translate(19 20) scale(0.6) translate(-12 -12)"
          class="stroke-ink-gray-5"
        >
          <path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z" />
          <path d="M12 22V12" />
          <path d="m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7" />
          <path d="m7.5 4.27 9 5.15" />
        </g>

        <rect x="34" y="13" width="32" height="4" rx="2" class="fill-ink-gray-4" />
        <rect x="34" y="22" width="22" height="4" rx="2" class="fill-surface-gray-3" />
      </g>

      <circle cx="160" cy="196" r="64" fill-opacity="0.25" class="fill-surface-gray-2" />
      <circle cx="160" cy="196" r="46" fill-opacity="0.55" class="fill-surface-gray-2" />
      <circle cx="160" cy="196" r="28" class="fill-surface-gray-3" />

      <path
        transform="translate(160 196) scale(1.75) translate(-8 -8)"
        d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"
        class="fill-ink-gray-7"
      />
    </g>

    <g v-else-if="step === 'storage'">
      <g class="spin" style="transform-origin: 160px 210px">
        <circle cx="160" cy="210" r="120" fill="none" stroke-dasharray="2 6" class="stroke-ink-gray-3" />
        <circle cx="160" cy="210" r="160" fill="none" stroke-dasharray="2 6" class="stroke-ink-gray-3" />
        <circle cx="280" cy="210" r="2.5" class="fill-ink-gray-3" />
        <circle cx="40" cy="210" r="2.5" class="fill-ink-gray-3" />
      </g>

      <circle cx="160" cy="92" r="64" fill-opacity="0.25" class="fill-surface-gray-2" />
      <circle cx="160" cy="92" r="46" fill-opacity="0.55" class="fill-surface-gray-2" />
      <circle cx="160" cy="92" r="28" class="fill-surface-gray-3" />
      <g
        fill="none"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        transform="translate(160 92) scale(1.1) translate(-12 -12)"
        class="stroke-ink-gray-7"
      >
        <path d="M12 13v8" />
        <path d="m8 17 4-4 4 4" />
        <path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" />
      </g>

      <path d="M160 176 V122" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />

      <rect x="92" y="200" width="136" height="56" rx="10" class="fill-surface-elevation-1 stroke-ink-gray-3" />
      <rect x="84" y="188" width="152" height="56" rx="10" class="fill-surface-elevation-1 stroke-ink-gray-3" />

      <g>
        <rect x="76" y="176" width="168" height="56" rx="10" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <rect x="88" y="190" width="28" height="28" rx="6" class="fill-surface-gray-2" />
        <g
          fill="none"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          transform="translate(102 204) scale(0.65) translate(-12 -12)"
          class="stroke-ink-gray-5"
        >
          <rect x="2" y="3" width="20" height="5" rx="1" />
          <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" />
          <path d="M10 12h4" />
        </g>

        <rect x="126" y="196" width="70" height="5" rx="2.5" class="fill-ink-gray-4" />
        <rect x="126" y="208" width="46" height="4" rx="2" class="fill-surface-gray-3" />
        <circle cx="228" cy="204" r="4" class="fill-ink-gray-6" />
      </g>

      <path d="M62 290 C62 270 80 258 96 256" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />
      <path d="M258 290 C258 270 240 258 224 256" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />

      <g>
        <rect x="40" y="290" width="44" height="44" rx="11" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <g
          fill="none"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          transform="translate(62 312) scale(0.75) translate(-12 -12)"
          class="stroke-ink-gray-5"
        >
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M3 5V19A9 3 0 0 0 21 19V5" />
          <path d="M3 12A9 3 0 0 0 21 12" />
        </g>
      </g>

      <g>
        <rect x="236" y="290" width="44" height="44" rx="11" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <g
          fill="none"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          transform="translate(258 312) scale(0.75) translate(-12 -12)"
          class="stroke-ink-gray-5"
        >
          <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
          <path d="M14 2v4a2 2 0 0 0 2 2h4" />
        </g>
      </g>
    </g>

    <g v-else>
      <g class="spin" style="transform-origin: 160px 205px">
        <circle cx="160" cy="205" r="120" fill="none" stroke-dasharray="2 6" class="stroke-ink-gray-3" />
        <circle cx="160" cy="205" r="160" fill="none" stroke-dasharray="2 6" class="stroke-ink-gray-3" />
        <circle cx="160" cy="85" r="2.5" class="fill-ink-gray-3" />
        <circle cx="160" cy="325" r="2.5" class="fill-ink-gray-3" />
      </g>

      <path d="M78 98 C90 112 92 124 92 132" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />
      <path d="M242 98 C230 112 228 124 228 132" fill="none" stroke-dasharray="3 5" class="stroke-ink-gray-3" />
      <path d="M74 314 C88 300 92 292 92 282" fill="none" stroke-dasharray="3 5" class="stroke-ink-gray-3" />
      <path d="M246 314 C232 300 228 292 228 282" fill="none" stroke-width="1.2" class="flow stroke-ink-gray-5" />

      <g>
        <rect x="44" y="58" width="44" height="44" rx="11" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <path
          d="M58 72 h16 v16 h-16 z M58 80 h16 M66 72 v16"
          fill="none"
          stroke-width="1.4"
          stroke-linejoin="round"
          class="stroke-ink-gray-5"
        />
      </g>

      <g>
        <rect x="232" y="58" width="44" height="44" rx="11" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <circle cx="254" cy="76" r="5" fill="none" stroke-width="1.4" class="stroke-ink-gray-5" />
        <path
          d="M245 90 C247 84 261 84 263 90"
          fill="none"
          stroke-width="1.4"
          stroke-linecap="round"
          class="stroke-ink-gray-5"
        />
      </g>

      <g>
        <rect x="40" y="306" width="44" height="44" rx="11" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <path
          d="M53 320 h18 v12 h-10 l-5 4 v-4 h-3 z"
          fill="none"
          stroke-width="1.4"
          stroke-linejoin="round"
          class="stroke-ink-gray-5"
        />
      </g>

      <g>
        <rect x="236" y="306" width="44" height="44" rx="11" class="fill-surface-elevation-1 stroke-ink-gray-3" />
        <path
          d="M249 333 l6 -8 5 5 7 -10"
          fill="none"
          stroke-width="1.4"
          stroke-linecap="round"
          stroke-linejoin="round"
          class="stroke-ink-gray-5"
        />
      </g>

      <rect x="52" y="132" width="216" height="150" rx="12" class="fill-surface-elevation-1 stroke-ink-gray-3" />
      <path d="M52 158 H268" class="stroke-ink-gray-3" />

      <g class="fill-ink-gray-3">
        <circle cx="66" cy="145" r="3.5" />
        <circle cx="78" cy="145" r="3.5" />
        <circle cx="90" cy="145" r="3.5" />
      </g>

      <rect x="104" y="138" width="124" height="14" rx="7" class="fill-surface-gray-2" />
      <circle cx="114" cy="145" r="2.5" class="fill-ink-gray-6" />
      <rect x="122" y="143" width="62" height="4" rx="2" class="fill-ink-gray-4" />
      <rect x="68" y="172" width="96" height="8" rx="4" class="fill-ink-gray-4" />
      <rect x="68" y="186" width="140" height="5" rx="2.5" class="fill-surface-gray-3" />

      <rect x="68" y="208" width="58" height="56" rx="7" class="fill-surface-gray-1 stroke-ink-gray-3" />
      <rect x="134" y="208" width="58" height="56" rx="7" class="fill-surface-gray-1 stroke-ink-gray-3" />
      <rect
        x="200"
        y="208"
        width="52"
        height="56"
        rx="7"
        fill="none"
        stroke-dasharray="3 4"
        class="stroke-ink-gray-3"
      />

      <rect x="76" y="218" width="26" height="5" rx="2.5" class="fill-ink-gray-4" />
      <rect x="76" y="228" width="40" height="4" rx="2" class="fill-surface-gray-3" />
      <rect x="142" y="218" width="34" height="5" rx="2.5" class="fill-ink-gray-4" />
      <rect x="142" y="228" width="30" height="4" rx="2" class="fill-surface-gray-3" />
      <path d="M226 230 v12 M220 236 h12" stroke-width="1.4" stroke-linecap="round" class="stroke-ink-gray-5" />
      <circle cx="262" cy="134" r="4" class="fill-ink-gray-6" />
    </g>
  </svg>
</template>

<style scoped>
:global(:root:not([data-theme='dark']) .setup-art) {
  --ink-gray-2: oklch(0.87 0 0);
  --ink-gray-3: oklch(0.76 0 0);
  --ink-gray-4: oklch(0.63 0 0);
  --ink-gray-5: oklch(0.52 0 0);
  --ink-gray-6: oklch(0.4 0 0);
  --ink-gray-7: oklch(0.31 0 0);
  --surface-gray-2: oklch(0.94 0 0);
  --surface-gray-3: oklch(0.91 0 0);
}

@media (prefers-reduced-motion: no-preference) {
  .flow {
    animation: dash 2.2s linear infinite;
  }

  .spin {
    transform-box: view-box;
    animation: spin 80s linear infinite;
  }
}

.flow {
  stroke-dasharray: 3 5;
}

@keyframes dash {
  to {
    stroke-dashoffset: -16;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
