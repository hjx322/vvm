<script setup>
import { ref, watch, onMounted } from 'vue'

// 医生管理：列表 / 新增 / 改名 / 删除 / 展开查看并启停技能
const props = defineProps({
  userId: { type: String, default: '' },
})

const agents = ref([])
const loading = ref(false)
const msg = ref('') // 顶部一次性提示（成功/错误）

// 新增表单
const newName = ref('')
const creating = ref(false)

// 行内编辑
const editingId = ref('')
const editName = ref('')

// 展开详情（技能启停）
const expandedId = ref('')
const detailSkills = ref([]) // [{skill_id, description, language, is_enabled}]
const detailLoading = ref(false)

async function api(url, options) {
  const resp = await fetch(url, options)
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

async function loadAgents() {
  if (!props.userId) return
  loading.value = true
  msg.value = ''
  try {
    const r = await api(`/api/v1/agents?user_id=${encodeURIComponent(props.userId)}&page_size=100`)
    agents.value = r.data || []
  } catch (e) {
    msg.value = `加载医生列表失败：${e.message} — 请确认 8000 管理服务已启动（uvicorn backend.main:app --port 8000）`
  } finally {
    loading.value = false
  }
}

async function createAgent() {
  const name = newName.value.trim()
  if (!name) { msg.value = '请先填写医生名称'; return }
  creating.value = true
  msg.value = ''
  try {
    await api(`/api/v1/agents?agent_name=${encodeURIComponent(name)}&user_id=${encodeURIComponent(props.userId)}`, { method: 'POST' })
    newName.value = ''
    await loadAgents()
  } catch (e) {
    msg.value = `新增失败：${e.message}`
  } finally {
    creating.value = false
  }
}

function startEdit(agent) {
  editingId.value = agent.agent_id
  editName.value = agent.agent_name
}

function cancelEdit() {
  editingId.value = ''
  editName.value = ''
}

async function saveEdit(agent) {
  const name = editName.value.trim()
  if (!name) { msg.value = '名称不能为空'; return }
  try {
    await api(`/api/v1/agents/${agent.agent_id}?user_id=${encodeURIComponent(props.userId)}&agent_name=${encodeURIComponent(name)}`, { method: 'PUT' })
    cancelEdit()
    await loadAgents()
  } catch (e) {
    msg.value = `修改失败：${e.message}`
  }
}

async function removeAgent(agent) {
  if (!window.confirm(`确认删除医生「${agent.agent_name}」？将同时解除其所有技能关联。`)) return
  try {
    await api(`/api/v1/agents/${agent.agent_id}?user_id=${encodeURIComponent(props.userId)}`, { method: 'DELETE' })
    if (expandedId.value === agent.agent_id) { expandedId.value = ''; detailSkills.value = [] }
    await loadAgents()
  } catch (e) {
    msg.value = `删除失败：${e.message}`
  }
}

// 拉取单个医生的技能启停明细（不切换展开状态，供 toggleExpand / toggleSkill 复用）
async function refreshDetail(agentId) {
  detailLoading.value = true
  try {
    const r = await api(`/api/v1/agents/${agentId}?user_id=${encodeURIComponent(props.userId)}`)
    // 合并 enabled/disabled 成统一列表，便于按 is_enabled 开关
    detailSkills.value = [
      ...(r.enabled_skills || []).map((s) => ({ ...s, is_enabled: true })),
      ...(r.disabled_skills || []).map((s) => ({ ...s, is_enabled: false })),
    ]
  } catch (e) {
    msg.value = `获取技能列表失败：${e.message}`
  } finally {
    detailLoading.value = false
  }
}

async function toggleExpand(agent) {
  if (expandedId.value === agent.agent_id) {
    expandedId.value = ''
    detailSkills.value = []
    return
  }
  expandedId.value = agent.agent_id
  await refreshDetail(agent.agent_id)
}

async function toggleSkill(agent, skill) {
  const action = skill.is_enabled ? 'disable' : 'enable'
  try {
    await api(`/api/v1/agents/${agent.agent_id}/skills/${skill.skill_id}/${action}?user_id=${encodeURIComponent(props.userId)}`, { method: 'POST' })
    await refreshDetail(agent.agent_id)
  } catch (e) {
    msg.value = `切换技能失败：${e.message}`
  }
}

watch(() => props.userId, loadAgents)
onMounted(loadAgents)
</script>

<template>
  <div class="admin-panel">
    <div class="admin-head">
      <h2>医生管理</h2>
      <span class="hint">当前租户 user_id：{{ userId }}</span>
    </div>

    <p v-if="msg" class="tip">{{ msg }}</p>

    <!-- 新增 -->
    <div class="create-row">
      <input v-model="newName" placeholder="新医生名称" @keydown.enter="createAgent" />
      <button class="primary" @click="createAgent" :disabled="creating || !userId">新增医生</button>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else class="agent-list">
      <div v-for="agent in agents" :key="agent.agent_id" class="agent-card">
        <div class="agent-row">
          <!-- 第一行：医生名 + 展开箭头（编辑态时显示输入框） -->
          <div class="agent-info" @click="!editingId ? toggleExpand(agent) : null">
            <template v-if="editingId === agent.agent_id">
              <input v-model="editName" class="edit-input" aria-label="医生名称" @keydown.enter="saveEdit(agent)" />
            </template>
            <template v-else>
              <strong>{{ agent.agent_name }}</strong>
              <span class="caret">{{ expandedId === agent.agent_id ? '▾' : '▸' }}</span>
            </template>
          </div>
          <div class="agent-ops">
            <template v-if="editingId === agent.agent_id">
              <button class="primary" @click="saveEdit(agent)">保存</button>
              <button class="ghost" @click="cancelEdit">取消</button>
            </template>
            <template v-else>
              <button class="ghost" @click="startEdit(agent)">改名</button>
              <button class="danger-ghost" @click="removeAgent(agent)">删除</button>
            </template>
          </div>
        </div>
        <!-- 第二行：agent_id 小字（超长省略号截断，不再挤坏第一行） -->
        <div class="agent-sub-row">
          <code class="aid" :title="agent.agent_id">{{ agent.agent_id }}</code>
        </div>

        <!-- 技能启停明细 -->
        <div v-if="expandedId === agent.agent_id" class="agent-skills">
          <div v-if="detailLoading" class="muted">技能加载中…</div>
          <template v-else>
            <div v-if="detailSkills.length === 0" class="muted">该医生暂无可用技能</div>
            <div v-for="s in detailSkills" :key="s.skill_id" class="skill-item">
              <div class="skill-meta">
                <strong>{{ s.skill_id }}</strong>
                <span v-if="s.description" class="muted">{{ s.description }}</span>
              </div>
              <button
                class="toggle"
                :class="s.is_enabled ? 'on' : 'off'"
                @click="toggleSkill(agent, s)"
              >{{ s.is_enabled ? '已启用' : '未启用' }}</button>
            </div>
          </template>
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
.muted { color: var(--text-soft); font-size: 13px; }
.create-row { display: flex; gap: 8px; margin-bottom: 16px; }
.create-row input { flex: 1; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg); color: var(--text-h); }
.agent-list { display: flex; flex-direction: column; gap: 10px; }
.agent-card {
  border: 1px solid var(--border); border-radius: var(--radius-md); padding: 10px 12px;
  background: var(--panel);          /* 白框：浅灰底 → 白底，与整体白框统一 */
  box-shadow: var(--shadow-sm);      /* 轻微阴影，增强卡片感 */
}
.agent-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
/* 第一行：名字区可收缩截断，操作按钮区不换行 */
.agent-info { display: flex; align-items: center; gap: 8px; cursor: pointer; flex: 1; min-width: 0; }
.agent-info strong { color: var(--text-h); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.aid { font-size: 12px; color: var(--text-soft); background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.caret { color: var(--accent); font-size: 12px; flex: none; }
/* 第二行：agent_id 小字单行，超长省略号 */
.agent-sub-row { display: flex; margin-top: 6px; min-width: 0; }
.agent-sub-row .aid { max-width: 100%; }
.agent-ops { display: flex; gap: 6px; align-items: center; flex: none; flex-wrap: nowrap; }
.edit-input { flex: 1; min-width: 0; padding: 6px 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--panel); color: var(--text-h); }
button { border: 1px solid var(--border); background: var(--panel); color: var(--text-h); border-radius: var(--radius-sm); padding: 5px 12px; cursor: pointer; font-size: 13px; }
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: .5; cursor: not-allowed; }
.primary { background: var(--accent-grad); border-color: transparent; color: #fff; }
.ghost { box-shadow: var(--shadow-sm); }
.danger-ghost { color: var(--danger); }
.danger-ghost:hover:not(:disabled) { border-color: var(--danger); color: var(--danger); }
.agent-skills { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.skill-item { display: flex; justify-content: space-between; align-items: center; gap: 10px; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px 10px; }
.skill-meta { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.skill-meta strong { font-size: 13px; color: var(--text-h); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.skill-meta .muted { font-size: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.toggle { min-width: 76px; white-space: nowrap; }
.toggle.on { background: var(--med-green); border-color: transparent; color: #fff; }
.toggle.off { background: var(--panel); color: var(--text); }
</style>