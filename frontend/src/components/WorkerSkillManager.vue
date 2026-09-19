<script setup>
import { ref, watch, onMounted } from 'vue'

// 子 agent 技能管理：多 agent 下按子 agent(agent) 分配技能
// 取代原"医生管理"的按医生启停技能；子 agent 即多 agent 的 patient_knowledge/search 等。
const props = defineProps({
  userId: { type: String, default: '' },
})

const agents = ref([])
const loading = ref(false)
const msg = ref('') // 顶部一次性提示（成功/错误）

// 展开详情（技能启停）
const expandedName = ref('')
const detailSkills = ref([]) // [{skill_id, description, language, is_builtin, is_enabled}]
const detailLoading = ref(false)

async function api(url, options) {
  const resp = await fetch(url, options)
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

// silent=true：静默刷新（不显示"加载中…"、不清空列表），用于启停技能后同步计数
async function loadAgents(silent = false) {
  if (!props.userId) return
  if (!silent) loading.value = true
  msg.value = ''
  try {
    const r = await api(`/api/v1/agents?user_id=${encodeURIComponent(props.userId)}`)
    agents.value = r.data || []
  } catch (e) {
    msg.value = `加载子 agent 列表失败：${e.message} — 请确认 8000/8001 后端已启动`
  } finally {
    if (!silent) loading.value = false
  }
}

// 把后端算出的启用数回写到列表项：数字一律以后端为准，前端不写死、不自行累加
function syncEnabledCount(agentName, count) {
  if (typeof count !== 'number') return
  const target = agents.value.find((a) => a.agent_name === agentName)
  if (target) target.enabled_count = count
}

// 拉取单个子 agent 的技能启停明细（展开/切换后刷新）
// 明细接口同样回传 enabled_count，顺手同步列表头部的"N 个技能已启用"；
// 此前这里只更新 detailSkills，导致该数字停留在此前拉取的值、点启停看着"永远不变"。
async function refreshDetail(agentName) {
  detailLoading.value = true
  try {
    const r = await api(`/api/v1/agents/${encodeURIComponent(agentName)}/skills?user_id=${encodeURIComponent(props.userId)}`)
    detailSkills.value = r.skills || []
    syncEnabledCount(agentName, r.enabled_count)
  } catch (e) {
    msg.value = `获取技能列表失败：${e.message}`
  } finally {
    detailLoading.value = false
  }
}

async function toggleExpand(agent) {
  if (expandedName.value === agent.agent_name) {
    expandedName.value = ''
    detailSkills.value = []
    return
  }
  expandedName.value = agent.agent_name
  await refreshDetail(agent.agent_name)
}

async function toggleSkill(agent, skill) {
  const action = skill.is_enabled ? 'disable' : 'enable'
  try {
    await api(
      `/api/v1/agents/${encodeURIComponent(agent.agent_name)}/skills/${encodeURIComponent(skill.skill_id)}/${action}?user_id=${encodeURIComponent(props.userId)}`,
      { method: 'POST' },
    )
    await refreshDetail(agent.agent_name)
    await loadAgents(true) // 静默全量同步：所有子 agent 的"已启用"数量都与后端一致
  } catch (e) {
    msg.value = `切换技能失败：${e.message}`
  }
}

// 注意：watch 回调不能直接把 loadAgents 当 handler 传（会被传入 newVal/oldVal 当成 silent=true）
watch(() => props.userId, () => loadAgents())
onMounted(() => loadAgents())
</script>

<template>
  <div class="admin-panel">
    <div class="admin-head">
      <h2>Agent 技能分配</h2>
      <span class="hint">为多 agent 的每个子 agent 分别配置可用技能；不同子 agent 互不影响</span>
    </div>

    <p v-if="msg" class="tip">{{ msg }}</p>

    <!-- 列表 -->
    <div v-if="loading" class="muted">加载中…</div>
    <div v-else class="agent-list">
      <div v-if="agents.length === 0" class="muted">暂无子 agent 数据</div>
      <div v-for="a in agents" :key="a.agent_name" class="agent-card">
        <div class="agent-row" @click="toggleExpand(a)">
          <div class="agent-info">
            <strong>{{ a.agent_name }}</strong>
            <span v-if="a.description" class="muted desc">{{ a.description }}</span>
            <span class="muted">{{ a.enabled_count }} 个技能已启用</span>
            <span class="caret">{{ expandedName === a.agent_name ? '▾' : '▸' }}</span>
          </div>
        </div>

        <!-- 技能启停明细 -->
        <div v-if="expandedName === a.agent_name" class="agent-skills">
          <div v-if="detailLoading" class="muted">技能加载中…</div>
          <template v-else>
            <div v-if="detailSkills.length === 0" class="muted">该子 agent 暂无可用技能，请先在「技能」页上传</div>
            <div v-for="s in detailSkills" :key="s.skill_id" class="skill-item">
              <div class="skill-meta">
                <span class="skill-name">{{ s.skill_id }}<span v-if="s.is_builtin" class="badge">内置</span></span>
                <span v-if="s.description" class="muted">{{ s.description }}</span>
              </div>
              <button
                class="toggle"
                :class="s.is_enabled ? 'on' : 'off'"
                @click="toggleSkill(a, s)"
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
.admin-head h2 { margin: 0 0 8px; font-size: 18px; color: var(--text-h); }
.hint { font-size: 12px; color: var(--text-soft); }
.tip { font-size: 13px; color: var(--warn); margin: 0 0 10px; }
.muted { color: var(--text-soft); font-size: 13px; }
.agent-list { display: flex; flex-direction: column; gap: 10px; }
.agent-card {
  border: 1px solid var(--border); border-radius: var(--radius-md); padding: 10px 12px;
  background: var(--panel);
  box-shadow: var(--shadow-sm);
}
.agent-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; cursor: pointer; }
.agent-info { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; flex-wrap: wrap; }
.agent-info strong { color: var(--text-h); white-space: nowrap; }
.agent-info .desc { flex-basis: 100%; }
.caret { color: var(--accent); font-size: 12px; }
.agent-skills { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.skill-item { display: flex; justify-content: space-between; align-items: center; gap: 10px; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px 10px; }
.skill-meta { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.skill-name { font-size: 13px; color: var(--text-h); display: flex; align-items: center; gap: 6px; }
.skill-meta .muted { font-size: 12px; }
.badge { font-size: 11px; border-radius: 999px; padding: 1px 8px; background: var(--accent-bg); color: var(--accent); }
.toggle { min-width: 76px; white-space: nowrap; }
button { border: 1px solid var(--border); background: var(--panel); color: var(--text-h); border-radius: var(--radius-sm); padding: 5px 12px; cursor: pointer; font-size: 13px; }
button:hover:not(:disabled) { border-color: var(--accent); }
.toggle.on { background: var(--med-green); border-color: transparent; color: #fff; }
.toggle.off { background: var(--panel); color: var(--text); }
</style>
