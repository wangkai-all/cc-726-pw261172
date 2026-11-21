<script setup>
defineProps({
  records: { type: Array, default: () => [] },
  activeNo: { type: Number, default: null },
})
defineEmits(['select'])
</script>

<template>
  <div class="scroll tall">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>status</th>
          <th>enc</th>
          <th>offset</th>
          <th>decl / actual</th>
          <th>fields</th>
          <th>issues</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="rec in records"
          :key="rec.record_no"
          class="selectable"
          :class="{ active: rec.record_no === activeNo }"
          @click="$emit('select', rec.record_no)"
        >
          <td class="mono">{{ rec.record_no }}</td>
          <td><span class="badge" :class="rec.status">{{ rec.status }}</span></td>
          <td class="mono muted">{{ rec.encoding }}</td>
          <td class="mono muted">{{ rec.byte_offset }}</td>
          <td class="mono muted">{{ rec.declared_length }} / {{ rec.actual_length }}</td>
          <td class="mono">{{ rec.field_count }}</td>
          <td>
            <span v-if="rec.issue_count" class="badge recovered">{{ rec.issue_count }}</span>
            <span v-else class="muted">–</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
