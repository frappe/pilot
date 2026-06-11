<script setup>
defineProps({
  data: { default: null },
  depth: { type: Number, default: 0 },
})
</script>

<template>
  <div :class="depth > 0 ? 'ml-4 border-l border-outline-gray-1 pl-3' : ''">
    <!-- Object -->
    <template v-if="data !== null && typeof data === 'object' && !Array.isArray(data)">
      <div v-for="(val, key) in data" :key="key" class="py-0.5">
        <span class="font-medium text-ink-gray-6">{{ key }}</span>
        <span class="text-ink-gray-3 mx-1">:</span>
        <template v-if="val !== null && typeof val === 'object'">
          <ConfigTree :data="val" :depth="depth + 1" />
        </template>
        <span v-else-if="typeof val === 'string'" class="text-ink-green-2">"{{ val }}"</span>
        <span v-else-if="typeof val === 'number'" class="text-ink-blue-2">{{ val }}</span>
        <span v-else-if="typeof val === 'boolean'" class="text-ink-amber-2">{{ val }}</span>
        <span v-else class="text-ink-gray-4">null</span>
      </div>
    </template>

    <!-- Array -->
    <template v-else-if="Array.isArray(data)">
      <div v-for="(val, idx) in data" :key="idx" class="py-0.5">
        <span class="text-ink-gray-4">{{ idx }}</span>
        <span class="text-ink-gray-3 mx-1">:</span>
        <template v-if="val !== null && typeof val === 'object'">
          <ConfigTree :data="val" :depth="depth + 1" />
        </template>
        <span v-else-if="typeof val === 'string'" class="text-ink-green-2">"{{ val }}"</span>
        <span v-else-if="typeof val === 'number'" class="text-ink-blue-2">{{ val }}</span>
        <span v-else-if="typeof val === 'boolean'" class="text-ink-amber-2">{{ val }}</span>
        <span v-else class="text-ink-gray-4">null</span>
      </div>
    </template>

    <!-- Primitive fallback -->
    <template v-else>
      <span v-if="typeof data === 'string'" class="text-ink-green-2">"{{ data }}"</span>
      <span v-else-if="typeof data === 'number'" class="text-ink-blue-2">{{ data }}</span>
      <span v-else-if="typeof data === 'boolean'" class="text-ink-amber-2">{{ data }}</span>
      <span v-else class="text-ink-gray-4">null</span>
    </template>
  </div>
</template>
