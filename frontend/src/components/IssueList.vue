<script setup>
defineProps({ issues: { type: Array, default: () => [] } })
</script>

<template>
  <div v-if="issues.length" class="issues">
    <div
      v-for="(iss, i) in issues"
      :key="i"
      class="issue"
      :class="iss.level"
    >
      <span class="badge" :class="iss.level">{{ iss.level }}</span>
      <span class="code">{{ iss.code }}</span>
      <div>{{ iss.message }}</div>
      <div class="where">
        byte offset {{ iss.byte_offset }}<span v-if="iss.raw_hex"> · {{ iss.raw_hex }}</span>
      </div>
    </div>
  </div>
  <p v-else class="muted">No issues.</p>
</template>
