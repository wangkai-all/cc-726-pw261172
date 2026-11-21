<script setup>
import RawSegments from './RawSegments.vue'

defineProps({ record: { type: Object, required: true } })
</script>

<template>
  <div>
    <div class="panel" style="margin-bottom: 14px">
      <h3>Leader</h3>
      <table class="leader-tbl">
        <tr>
          <td class="muted">value</td>
          <td v-for="(ch, i) in record.leader.split('')" :key="i">
            <span :title="`position ${i}`">{{ ch === ' ' ? '␠' : ch }}</span>
          </td>
        </tr>
        <tr>
          <td class="muted">pos</td>
          <td v-for="i in 24" :key="i" class="muted">{{ i - 1 }}</td>
        </tr>
      </table>
      <div class="kv" style="margin-top: 10px">
        <span class="k">Declared length</span><span>{{ record.declared_length }} bytes</span>
        <span class="k">Actual span</span><span>{{ record.actual_length }} bytes</span>
        <span class="k">Base address</span><span>{{ record.base_address }}</span>
        <span class="k">Encoding (Leader/09)</span>
        <span><b>{{ record.encoding.toUpperCase() }}</b></span>
        <span class="k">Record offset in file</span>
        <span class="mono">{{ record.byte_offset }}</span>
      </div>
    </div>

    <div
      v-for="field in record.fields"
      :key="`${field.tag}-${field.order}`"
      class="field-card"
    >
      <div class="field-head">
        <span class="tag">{{ field.tag }}</span>
        <span class="badge neutral">{{ field.field_type }}</span>
        <span>occurrence #{{ field.order }}</span>
        <span class="offset">@{{ field.byte_offset }} · len {{ field.length }}</span>
        <span v-if="field.indicators" class="offset">
          indicators: [{{ field.indicators[0] === ' ' ? '␠' : field.indicators[0] }}]
          [{{ field.indicators[1] === ' ' ? '␠' : field.indicators[1] }}]
        </span>
      </div>
      <div class="field-body">
        <!-- Control field -->
        <template v-if="field.field_type === 'control'">
          <div class="sf-row">
            <span class="sf-code">value</span>
            <div>
              <RawSegments :segments="field.control_segments || []" />
              <div v-for="c in (field.control_decode?.combining || [])" :key="c.byte_offset"
                   class="combining-note">
                combining U+{{ c.char.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') }}
                {{ c.name }} @byte {{ c.byte_offset }} ({{ c.encoding }})
              </div>
            </div>
          </div>
        </template>

        <!-- Data field: every subfield occurrence kept, repeats ordered -->
        <template v-else>
          <div v-for="sf in field.subfields" :key="`${sf.code}-${sf.order}`" class="sf-row">
            <span class="sf-code">${{ sf.code }} <span class="sf-meta">#{{ sf.order }}</span></span>
            <div>
              <RawSegments :segments="sf.segments || []" />
              <div class="sf-meta">@{{ sf.byte_offset }} · {{ sf.raw_hex }}</div>
              <div v-for="c in sf.decode.combining" :key="c.byte_offset"
                   class="combining-note">
                combining U+{{ c.char.codePointAt(0).toString(16).toUpperCase().padStart(4, '0') }}
                {{ c.name }} @byte {{ c.byte_offset }} ({{ c.encoding }})
              </div>
            </div>
          </div>
          <p v-if="!field.subfields.length" class="muted">
            Field has no parseable subfields.
          </p>
        </template>

        <details>
          <summary class="muted" style="cursor: pointer; font-size: 12px">
            raw field bytes ({{ field.raw_hex.split(' ').length }} bytes, field terminator excluded)
          </summary>
          <div class="hex">{{ field.raw_hex }}</div>
        </details>
      </div>
    </div>

    <details class="panel">
      <summary class="muted" style="cursor: pointer">whole record raw bytes (hex)</summary>
      <div class="hex" style="max-height: 320px; margin-top: 10px">{{ record.raw_hex }}</div>
    </details>
  </div>
</template>
