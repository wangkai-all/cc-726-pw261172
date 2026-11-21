<script setup>
import { reactive, watch, computed } from 'vue'

const props = defineProps({
  scheme: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['change', 'save'])

// Local editable copy; mutating it emits a debounced 'change' so the
// preview panel updates immediately while the user types.
const draft = reactive(JSON.parse(JSON.stringify(props.scheme)))

watch(
  () => props.scheme,
  (next) => {
    Object.assign(draft, JSON.parse(JSON.stringify(next)))
  },
  { deep: false }
)

let timer = null
watch(
  draft,
  () => {
    clearTimeout(timer)
    timer = setTimeout(() => emit('change', JSON.parse(JSON.stringify(draft))), 250)
  },
  { deep: true }
)

const mainControl = computed(() => draft.main.filter((m) => m.kind === 'control'))
const mainData = computed(() => draft.main.filter((m) => m.kind === 'data'))
</script>

<template>
  <div :class="{ disabled }">
    <div class="row" style="margin-bottom: 12px">
      <label>Scheme name
        <input v-model="draft.name" style="width: 180px" :disabled="disabled" />
      </label>
      <input v-model="draft.description" style="flex: 1; min-width: 220px"
             :disabled="disabled" placeholder="description" />
    </div>

    <h3>Main table — control fields</h3>
    <div class="mapping-section">
      <div v-for="m in mainControl" :key="m.target" class="mapping-entry">
        <input type="checkbox" v-model="m.enabled" :disabled="disabled" />
        <span class="mono pill-count">{{ m.tag }}</span>
        <input v-model="m.target" style="width: 200px" :disabled="disabled || !m.enabled" />
        <label class="checkbox muted">
          <input type="checkbox" v-model="m.join" :disabled="disabled || !m.enabled" />
          join repeats
        </label>
        <input v-if="m.join" v-model="m.separator" style="width: 70px"
               placeholder="sep" :disabled="disabled" />
      </div>
    </div>

    <h3>Main table — data fields (array-valued, repeats preserved)</h3>
    <div class="mapping-section">
      <div v-for="m in mainData" :key="m.target" class="mapping-entry" style="display: block">
        <div class="row">
          <input type="checkbox" v-model="m.enabled" :disabled="disabled" />
          <span class="mono pill-count">{{ m.tag }}</span>
          <input v-model="m.target" style="width: 200px" :disabled="disabled || !m.enabled" />
          <span class="muted">one array entry per field occurrence</span>
        </div>
        <div style="margin: 8px 0 0 26px; display: flex; flex-direction: column; gap: 6px">
          <div v-for="col in m.columns" :key="col.subfield" class="row">
            <input type="checkbox" v-model="col.enabled" :disabled="disabled || !m.enabled" />
            <span class="mono sf-code" style="width: 34px">${{ col.subfield }}</span>
            <input v-model="col.target" style="width: 200px"
                   :disabled="disabled || !m.enabled || !col.enabled" />
            <label class="checkbox muted">
              <input type="checkbox" v-model="col.join"
                     :disabled="disabled || !m.enabled || !col.enabled" />
              join repeated subfields
            </label>
            <input v-if="col.join" v-model="col.separator" style="width: 70px"
                   placeholder="sep" :disabled="disabled" />
          </div>
        </div>
      </div>
    </div>

    <h3>Subtables — repeated fields as child rows (FK: record_no)</h3>
    <div class="mapping-section">
      <div v-for="st in draft.subtables" :key="st.name" class="mapping-entry" style="display: block">
        <div class="row">
          <input type="checkbox" v-model="st.enabled" :disabled="disabled" />
          <span class="mono pill-count">{{ st.tag }}</span>
          <input v-model="st.name" style="width: 200px" :disabled="disabled || !st.enabled" />
        </div>
        <div style="margin: 8px 0 0 26px; display: flex; flex-direction: column; gap: 6px">
          <div v-for="col in st.columns" :key="col.subfield" class="row">
            <input type="checkbox" v-model="col.enabled" :disabled="disabled || !st.enabled" />
            <span class="mono sf-code" style="width: 34px">${{ col.subfield }}</span>
            <input v-model="col.target" style="width: 200px"
                   :disabled="disabled || !st.enabled || !col.enabled" />
            <label class="checkbox muted">
              <input type="checkbox" v-model="col.join"
                     :disabled="disabled || !st.enabled || !col.enabled" />
              join repeats
            </label>
            <input v-if="col.join" v-model="col.separator" style="width: 70px"
                   placeholder="sep" :disabled="disabled" />
          </div>
        </div>
      </div>
      <p v-if="!draft.subtables.length" class="muted">No subtable tags present.</p>
    </div>

    <button class="primary" @click="emit('save', JSON.parse(JSON.stringify(draft)))">
      Save mapping scheme
    </button>
  </div>
</template>
