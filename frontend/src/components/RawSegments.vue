<script setup>
// Renders decoded text where unexplainable bytes are PUA-wrapped markers
// (already split by the backend into {type:'text'|'raw'} segments).
defineProps({ segments: { type: Array, default: () => [] } })
</script>

<template>
  <span class="segments">
    <template v-for="(seg, i) in segments" :key="i">
      <span v-if="seg.type === 'text'">{{ seg.value }}</span>
      <span
        v-else
        class="raw-mark"
        :title="`Unexplainable raw bytes retained (hex): ${seg.hex}`"
        >0x{{ seg.hex }}</span
      >
    </template>
  </span>
</template>
