<script setup>
import { ref, watch, onMounted } from 'vue'

// 技能管理：zip 上传 / 列表 / 删除
const props = defineProps({
  userId: { type: String, default: '' },
})

const skills = ref([])
const loading = ref(false)
const uploading = ref(false)
const msg = ref('')

const zipInput = ref(null)
const fileName = ref('')

async function api(url, options) {
  const resp = await fetch(url, options)
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

async function loadSkills() {
  if (!props.userId) return
  loading.value = true
  msg.value = ''
  try {
    const r = await api(`/api/v1/skills?user_id=${encodeURIComponent(props.userId)}&page_size=100`)
    skills.value = r.data || []
  } catch (e) {
    msg.value = `加载技能列表失败：${e.message} — 请确认 8000 管理服务已启动（uvicorn backend.main:app --port 8000）`
  } finally {
    loading.value = false
  }
}

function pickZip() {
  zipInput.value?.click()
}

function onZipSelected(event) {
  const f = event.target.files?.[0]
  fileName.value = f ? f.name : ''
}

async function uploadSkill() {
  const file = zipInput.value?.files?.[0]
  if (!file) { msg.value = '请先选择一个 .zip 技能包'; return }
  if (!file.name.toLowerCase().endsWith('.zip')) { msg.value = '仅支持 .zip 技能包'; return }

  uploading.value = true
  msg.value = ''
  try {
    const formData = new FormData()
    formData.append('file', file)
    const r = await api(`/api/v1/skills/upload?user_id=${encodeURIComponent(props.userId)}`, { method: 'POST', body: formData })
    msg.value = `✅ 技能「${r.skill_id}」上传成功，已落盘：${r.current_path}`
    fileName.value = ''
    if (zipInput.value) zipInput.value.value = ''
    await loadSkills()
  } catch (e) {
    msg.value = `上传失败：${e.message}`
  } finally {
    uploading.value = false
  }
}

async function removeSkill(skill) {
  if (skill.is_builtin) { msg.value = '内置技能不允许删除'; return }
  if (!window.confirm(`确认删除技能「${skill.skill_id}」？将移除文件并解除所有医生关联。`)) return
  try {
    await api(`/api/v1/skills/${encodeURIComponent(skill.skill_id)}?user_id=${encodeURIComponent(props.userId)}`, { method: 'DELETE' })
    await loadSkills()
  } catch (e) {
    msg.value = `删除失败：${e.message}`
  }
}

watch(() => props.userId, loadSkills)
onMounted(loadSkills)
</script>

<template>
  <div class="admin-panel">
    <div class="admin-head">
      <h2>技能管理</h2>
      <span class="hint">技能包需为 zip，内含带 name 字段的 SKILL.md</span>
    </div>

    <p v-if="msg" class="tip" :class="{ ok: msg.startsWith('✅') }">{{ msg }}</p>

    <!-- 上传 -->
    <div class="upload-row">
      <input
        ref="zipInput"
        type="file"
        accept=".zip,application/zip"
        style="display: none"
        @change="onZipSelected"
      />
      <button class="ghost" @click="pickZip" :disabled="uploading">选择 zip</button>
      <span class="muted">{{ fileName || '未选择文件' }}</span>
      <button class="primary" @click="uploadSkill" :disabled="uploading || !fileName">上传技能</button>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else class="skill-list">
      <div v-if="skills.length === 0" class="muted">暂无技能，请上传</div>
      <div v-for="s in skills" :key="s.skill_id" class="skill-card">
        <div class="skill-main">
          <strong>{{ s.skill_id }}</strong>
          <span v-if="s.is_builtin" class="badge">内置</span>
          <span v-else class="badge custom">自定义</span>
          <span class="muted">{{ s.description || '（无描述）' }}</span>
        </div>
        <div class="skill-ops">
          <code class="path">{{ s.current_path }}</code>
          <button v-if="!s.is_builtin" class="danger-ghost" @click="removeSkill(s)">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-panel {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 0;
  box-shadow: none;
  margin: 0;
  max-width: 100%;
}
.admin-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.admin-head h2 { margin: 0 0 14px; font-size: 18px; color: var(--text-h); }
.hint { font-size: 12px; color: var(--text-soft); }
.tip { font-size: 13px; color: var(--warn); margin: 0 0 10px; }
.tip.ok { color: var(--med-green); }
.muted { color: var(--text-soft); font-size: 13px; }
.upload-row { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
.upload-row button, .upload-row .muted { white-space: nowrap; }
button { border: 1px solid var(--border); background: var(--panel); color: var(--text-h); border-radius: var(--radius-sm); padding: 6px 14px; cursor: pointer; font-size: 13px; }
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: .5; cursor: not-allowed; }
.primary { background: var(--accent-grad); border-color: transparent; color: #fff; }
.ghost { box-shadow: var(--shadow-sm); }
.danger-ghost { color: var(--danger); }
.danger-ghost:hover:not(:disabled) { border-color: var(--danger); }
.skill-list { display: flex; flex-direction: column; gap: 8px; }
.skill-card {
  display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
  border: 1px solid var(--border); border-radius: var(--radius-md); padding: 9px 12px;
  background: var(--panel);      /* 白框：浅灰底 → 白底，与整体白框统一 */
  box-shadow: var(--shadow-sm);  /* 轻微阴影，增强卡片感 */
}
.skill-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.skill-main strong { color: var(--text-h); }
.badge { font-size: 11px; border-radius: 999px; padding: 1px 8px; background: var(--accent-bg); color: var(--accent); }
.badge.custom { background: rgba(47, 201, 138, .15); color: var(--med-green); }
.skill-ops { display: flex; align-items: center; gap: 10px; flex-wrap: nowrap; }
.path { font-size: 11px; color: var(--text-soft); background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>