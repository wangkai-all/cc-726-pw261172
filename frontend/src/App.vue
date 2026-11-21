<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from './api.js'
import RecordList from './components/RecordList.vue'
import FieldViewer from './components/FieldViewer.vue'
import MappingEditor from './components/MappingEditor.vue'
import IssueList from './components/IssueList.vue'

const dataset = ref(null)
const recordDetail = ref(null)
const activeNo = ref(null)
const scheme = ref(null)
const schemeName = ref('default')
const preview = ref(null)
const previewError = ref('')
const exportTicket = ref(null)
const toast = ref('')
const loading = ref(false)
const dragOver = ref(false)
let draftScheme = null

const statusCounts = computed(() => dataset.value?.status_counts || {})
const validRecordForPreview = computed(
  () => recordDetail.value && recordDetail.value.status !== 'invalid'
)

function showToast(msg) {
  toast.value = msg
  setTimeout(() => (toast.value = ''), 6000)
}

async function handleUpload(file) {
  if (!file) return
  loading.value = true
  try {
    const meta = await api.upload(file)
    await loadDataset(meta.dataset_id)
  } catch (e) {
    showToast(e.message)
  } finally {
    loading.value = false
  }
}

function onDrop(e) {
  dragOver.value = false
  const file = e.dataTransfer.files?.[0]
  handleUpload(file)
}

async function loadDataset(id) {
  dataset.value = await api.getDataset(id)
  const schemes = await api.listSchemes(id)
  schemeName.value = schemes.active_scheme
  scheme.value = schemes.schemes.find((s) => s.name === schemeName.value).scheme
  draftScheme = JSON.parse(JSON.stringify(scheme.value))
  await selectRecord(dataset.value.records[0]?.record_no ?? null)
}

async function selectRecord(no) {
  if (no == null) {
    recordDetail.value = null
    preview.value = null
    return
  }
  activeNo.value = no
  recordDetail.value = await api.getRecord(dataset.value.dataset_id, no)
  await refreshPreview()
}

async function refreshPreview() {
  preview.value = null
  previewError.value = ''
  if (!recordDetail.value || recordDetail.value.status === 'invalid') {
    previewError.value =
      'Structurally invalid record: it is isolated and excluded from the dataset.'
    return
  }
  const effective = draftScheme || scheme.value
  try {
    // Live-preview the unsaved draft without persisting it.
    if (draftScheme && JSON.stringify(draftScheme) !== JSON.stringify(scheme.value)) {
      preview.value = await api.previewDraft(
        dataset.value.dataset_id, recordDetail.value.record_no, draftScheme
      )
    } else {
      preview.value = await api.preview(
        dataset.value.dataset_id, recordDetail.value.record_no, schemeName.value
      )
    }
  } catch (e) {
    previewError.value = `Preview unavailable: ${e.message}`
  }
}

async function onDraftChange(next) {
  draftScheme = next
  await refreshPreview()
}

async function saveScheme(next) {
  try {
    await api.saveScheme(dataset.value.dataset_id, schemeName.value, next)
    scheme.value = next
    draftScheme = JSON.parse(JSON.stringify(next))
    showToast(`Scheme "${schemeName.value}" saved.`)
    await refreshPreview()
  } catch (e) {
    showToast(e.message)
  }
}

async function doExport(fmt) {
  try {
    // Persist the current draft first so the export matches what is shown.
    if (draftScheme) {
      await api.saveScheme(dataset.value.dataset_id, schemeName.value, draftScheme)
      scheme.value = draftScheme
    }
    exportTicket.value = await api.exportDataset(
      dataset.value.dataset_id, fmt, schemeName.value
    )
  } catch (e) {
    showToast(e.message)
  }
}

onMounted(async () => {
  try {
    await api.health()
  } catch {
    showToast('Backend unreachable — start uvicorn on :8000')
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <h1>MARC21 Ingest Station</h1>
      <span class="sub">ISO 2709 byte-level ingestion · field mapping · queryable export</span>
      <span class="spacer" />
      <span v-if="dataset" class="muted mono">{{ dataset.filename }} · {{ dataset.size_bytes }} B</span>
    </header>

    <main class="app-main">
      <!-- Upload -->
      <section v-if="!dataset" class="panel">
        <label
          class="dropzone"
          :class="{ drag: dragOver }"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop.prevent="onDrop"
        >
          <input type="file" accept=".mrc,.marc,.iso,application/octet-stream"
                 style="display:none"
                 @change="handleUpload($event.target.files[0])" />
          <div style="font-size: 16px; margin-bottom: 6px">
            Drop an ISO 2709 / MARC21 file here, or click to choose
          </div>
          <div class="muted">
            Splitting always follows Leader/directory byte lengths and offsets — never decoded characters.
          </div>
          <div v-if="loading" class="muted" style="margin-top: 8px">Parsing…</div>
        </label>
      </section>

      <template v-else>
        <!-- File-level status -->
        <section class="panel">
          <div class="row">
            <h2 style="margin: 0">Dataset overview</h2>
            <span class="badge ok">ok {{ statusCounts.ok || 0 }}</span>
            <span class="badge recovered">recovered {{ statusCounts.recovered || 0 }}</span>
            <span class="badge invalid">invalid {{ statusCounts.invalid || 0 }}</span>
            <span class="spacer" />
            <button @click="dataset = null; recordDetail = null">Upload another file</button>
          </div>
          <div v-if="dataset.file_issues.length" style="margin-top: 12px">
            <IssueList :issues="dataset.file_issues" />
            <p class="muted" style="font-size: 12px">
              A fatal issue means later bytes could not be located as records reliably, so parsing stopped.
            </p>
          </div>
        </section>

        <section class="grid-2">
          <!-- Left: record list -->
          <div class="panel">
            <h2>Records ({{ dataset.records.length }})</h2>
            <RecordList
              :records="dataset.records"
              :active-no="activeNo"
              @select="selectRecord"
            />
          </div>

          <!-- Right: record detail -->
          <div class="panel">
            <template v-if="recordDetail">
              <div class="row">
                <h2 style="margin: 0">
                  Record #{{ recordDetail.record_no }}
                  <span class="badge" :class="recordDetail.status">{{ recordDetail.status }}</span>
                </h2>
                <span class="spacer" />
                <a :href="`/api/datasets/${dataset.dataset_id}/records/${recordDetail.record_no}/raw`"
                   target="_blank">
                  <button>download raw record bytes</button>
                </a>
              </div>
              <IssueList :issues="recordDetail.issues" />
              <FieldViewer v-if="recordDetail.fields.length" :record="recordDetail" />
              <p v-else class="muted">
                No fields were split: the directory/leader is not trustworthy for this record.
                Raw bytes remain available via the download button and below.
              </p>
              <details v-if="!recordDetail.fields.length" class="panel" style="margin-top: 10px">
                <summary class="muted" style="cursor: pointer">raw bytes (hex)</summary>
                <div class="hex" style="max-height: 320px; margin-top: 10px">{{ recordDetail.raw_hex }}</div>
              </details>
            </template>
            <p v-else class="muted">Select a record to inspect.</p>
          </div>
        </section>

        <!-- Mapping + live preview -->
        <section class="grid-mapping">
          <div class="panel">
            <h2>Mapping scheme</h2>
            <p class="muted" style="font-size: 12px">
              Edits update the preview immediately. Repeated fields/subfields stay ordered arrays;
              child tables link back through <code>record_no</code>.
            </p>
            <MappingEditor
              :scheme="scheme"
              @change="onDraftChange"
              @save="saveScheme"
            />
          </div>

          <div class="panel">
            <h2>
              Live expansion
              <span class="muted" style="font-weight: 400">
                of record #{{ activeNo }}
              </span>
            </h2>
            <p v-if="previewError" class="issue error">{{ previewError }}</p>
            <pre v-else-if="preview" class="preview-json">{{
              JSON.stringify(preview.expanded, null, 2)
            }}</pre>
            <p v-else class="muted">No preview.</p>

            <div class="row" style="margin-top: 14px">
              <h3 style="margin: 0">Export queryable dataset</h3>
              <span class="spacer" />
              <button class="primary" @click="doExport('sqlite')">SQLite (.sqlite3)</button>
              <button @click="doExport('jsonl')">JSON Lines (.jsonl)</button>
              <button @click="doExport('csv')">CSV bundle (.zip)</button>
            </div>
            <div v-if="exportTicket" class="panel" style="margin-top: 12px; background: var(--panel-2)">
              <div class="row">
                <span class="badge ok">{{ exportTicket.format }}</span>
                <span class="mono">{{ exportTicket.main_row_count }} main rows</span>
                <span
                  v-for="(cnt, name) in exportTicket.subtable_counts"
                  :key="name"
                  class="pill-count mono"
                >{{ name }}: {{ cnt }}</span>
                <span v-if="exportTicket.skipped_records.length" class="badge invalid">
                  skipped {{ exportTicket.skipped_records.length }} invalid
                </span>
                <span class="spacer" />
                <a :href="exportTicket.download_path" target="_blank">
                  <button class="primary">download</button>
                </a>
              </div>
              <p class="muted" style="font-size: 12px; margin: 8px 0 0">
                Every row carries record_no and byte offsets; invalid records are listed, not silently dropped.
              </p>
            </div>
          </div>
        </section>
      </template>
    </main>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>
