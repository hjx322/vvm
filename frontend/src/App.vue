<script setup>
import { ref, nextTick, onMounted } from 'vue'
import WorkerSkillManager from './components/WorkerSkillManager.vue'
import SkillManager from './components/SkillManager.vue'

// ---------- 状态 ----------
// 消息列表：{ role: 'user' | 'assistant', content: string }
const messages = ref([])
const input = ref('')
const sending = ref(false) // 是否正在请求（用于禁用/停止）
let abortController = null // 用于停止流式请求

// 图片上传相关
const fileInput = ref(null)    // 隐藏的文件选择 input
const previewUrl = ref('')     // 已选图片的预览 URL（blob）
const uploadResult = ref(null) // 上传结果 { path, filename, size }
const uploading = ref(false)   // 是否正在上传

// 空状态欢迎屏的示例问题（点击直接发起对话）
const suggestions = [
  '皮肤出现红疹怎么办？',
  '日常如何科学防晒？',
  '胳膊上长了个小疙瘩，需要处理吗？',
  '敏感肌应该如何选择护肤品？',
]

// 会话参数（默认值与后端一致；一次会话固定 workflowId 以保留多轮记忆）
const params = ref({
  crm: 'hn',
  chatName: '陈怡冰 1881921',  // 会话名：默认患者；选中患者后自动填「姓名 病历号」
  doctorId: 'agt_d75e25a434fa457f',  // 对话 doctor_id（多 agent 模式下技能按子 agent 分配，此值仅保 checkpoint 兼容，固定不再下拉）
  userId: '1827196',       // 租户/用户 id：技能/子 agent 配置均挂在 1827196 名下，勿改
  medicalRecordNo: '1881921',  // 默认患者的显式病历号，透传给后端绕过正则提取
  workflowId: localStorage.getItem('doctor_workflow_id') || crypto.randomUUID(),
})
let roundCount = 0 // 首轮 restart=true

// ---------- 患者搜索（下拉选择） ----------
const patientResults = ref([])  // [{medical_record_no, name, phone}]
const patientSearching = ref(false)
let patientTimer = null

// 全量患者列表（原生下拉用）：onMounted 一次拉全，选中即回填姓名+病历号
const allPatients = ref([])  // [{medical_record_no, name, phone}]
const patientsLoaded = ref(false)
const patientsError = ref('') // 患者列表加载失败原因（8001 未启动 / 旧代码返回 422 等）

async function searchPatients() {
  const kw = params.value.chatName.trim()
  if (!kw) { patientResults.value = []; return }
  patientSearching.value = true
  try {
    const resp = await fetch(`/api/patients?keyword=${encodeURIComponent(kw)}&limit=10`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const j = await resp.json()
    patientResults.value = j.data || []
  } catch {
    patientResults.value = []
  } finally {
    patientSearching.value = false
  }
}

function debouncedSearchPatients() {
  clearTimeout(patientTimer)
  patientTimer = setTimeout(searchPatients, 300)
}

function selectPatient(p) {
  params.value.chatName = `${p.name} ${p.medical_record_no}`
  params.value.medicalRecordNo = p.medical_record_no
  patientResults.value = []
}

// ---------- 患者原生下拉（全量） ----------
// 注：上方 searchPatients / debouncedSearchPatients / selectPatient 已被原生下拉替代，保留备用。
// 一次性拉取该租户全量患者（keyword 为空时后端返回全部），供原生 <select> 直接列出。
async function loadAllPatients() {
  if (patientsLoaded.value) return
  try {
    const resp = await fetch(`/api/patients?limit=1000`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const j = await resp.json()
    allPatients.value = j.data || []
    patientsLoaded.value = true
  } catch (e) {
    // 失败也立即结束"加载中"，给出可操作提示（避免一直转圈）：
    // HTTP 422 = 8001 还是旧代码未重启（limit 上限旧为 50）；fetch 网络错误 = 8001 未启动
    patientsError.value = `${e?.message || e} — 请确认 8001 对话服务已启动（uvicorn backend.chat_server:app --port 8001）`
    patientsLoaded.value = true
  }
}

// 错误提示条上的重试：重置加载态后重新拉取患者列表
function retryPatients() {
  patientsError.value = ''
  patientsLoaded.value = false
  allPatients.value = []
  loadAllPatients()
}

// 原生下拉选中事件：按病历号在 allPatients 中找到患者，自动回填「姓名 病历号」
function onPatientSelect() {
  const mrn = params.value.medicalRecordNo
  const p = allPatients.value.find((x) => x.medical_record_no === mrn)
  if (p) {
    params.value.chatName = `${p.name} ${p.medical_record_no}`
  } else {
    // 下拉被清空时恢复默认：回到默认患者「陈怡冰 1881921」
    params.value.chatName = '陈怡冰 1881921'
    params.value.medicalRecordNo = '1881921'
  }
}

// ---------- 管理视图（侧栏常驻，不再切换主区） ----------
// 子 agent 技能分配 / 技能管理 直接内嵌在左侧栏，adminTab 用于侧栏内 tabs 切换
const adminTab = ref('agents')

onMounted(() => {
  loadAllPatients()
  // 让"关闭页面后下次打开还能继续对话"生效：
  // ① 首次建档时把 workflowId 持久化到本地（后端 checkpoint 按它恢复状态）；② 打开时拉回历史回显。
  if (!localStorage.getItem('doctor_workflow_id')) {
    localStorage.setItem('doctor_workflow_id', params.value.workflowId)
  }
  loadHistory()
})

// 按当前 workflowId 从后端拉历史消息，实现"重开页面还能看到上次对话"
async function loadHistory() {
  try {
    const resp = await fetch(`/api/history?workflow_id=${encodeURIComponent(params.value.workflowId)}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const j = await resp.json()
    // 后端返回 { messages: [{role, content}] }；assistant 消息需包成 blocks 结构才能被打字机引擎/模板渲染
    messages.value = (j.messages || []).map((m) =>
      m.role === 'user'
        ? { role: 'user', content: m.content }
        : { role: 'assistant', blocks: [{ type: 'content', text: m.content }] }
    )
    scrollToBottom()
  } catch (e) {
    // 拉历史失败不阻塞聊天（新会话/后端旧版本时返回空或报错，忽略即可）
    console.warn('加载历史消息失败:', e)
  }
}

// 消息区容器，用于自动滚动到底部
const scrollBox = ref(null)
function scrollToBottom() {
  nextTick(() => {
    if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight
  })
}

// ---------- 思考过程打字机动画（前端逐字滚动、逐渐输出） ----------
// 原理：SSE 到达的 thought 完整文本存进 b.text；真正显示的是 b.text.slice(0, b.shownCount)。
// rAF 引擎按固定速率推进 shownCount，造成"动画滚动、逐渐输出"；若到达文本积压超过
// MAX_LAG 字符则直接追赶一段，避免思考过程拖太久（仍保留动画感）。
const TYPE_MS = 24       // 思考过程逐字符间隔（ms），约 40~60 字/秒
const MAX_LAG = 300      // 思考过程动画最多滞后于源文本的字符数，超出即追平
const TYPE_CONTENT_MS = 14  // 正式回复逐字符间隔（ms），更快的打字机，避免长回答播太久
const MAX_LAG_CONTENT = 80  // 正式回复最多滞后字符数，超出只追平这一段（不整段闪现）
let typeRaf = null       // 当前 rAF id（引擎运行中非 null，空闲时释放）
let typeLast = 0         // 上一帧时间戳，用于计算本帧应推进的字符数

function ensureTicker() {
  if (typeRaf) return
  typeLast = performance.now()
  typeRaf = requestAnimationFrame(typeTick)
}

function typeTick(now) {
  const elapsed = now - typeLast
  typeLast = now
  let pending = false
  for (const m of messages.value) {
    if (m.role !== 'assistant') continue
    for (const b of m.blocks) {
      // content 与 thought 都走打字机：thought 收起时暂停；content 必须平滑播完，避免"突然整段出现"
      if (b.collapsed) continue
      const isContent = b.type === 'content'
      const len = (b.text || '').length
      const shown = b.shownCount ?? len
      if (shown >= len) continue
      const ms = isContent ? TYPE_CONTENT_MS : TYPE_MS
      const maxLag = isContent ? MAX_LAG_CONTENT : MAX_LAG
      const step = Math.max(1, Math.round(elapsed / ms))
      if (len - shown > maxLag) {
        b.shownCount = len - maxLag
      } else {
        b.shownCount = Math.min(len, shown + step)
      }
      pending = true
    }
  }
  if (pending) {
    scrollToBottom()
    typeRaf = requestAnimationFrame(typeTick)
  } else {
    typeRaf = null // 全部播完，引擎空闲
  }
}

// 折叠开关与打字机联动：展开时重新驱动动画（收起期间暂停的进度继续播放）
function toggleThought(b) {
  b.collapsed = !b.collapsed
  if (!b.collapsed) ensureTicker()
}

// ---------- 发送 ----------
async function sendMessage() {
  const text = input.value.trim()
  const hasImage = !!uploadResult.value
  if ((!text && !hasImage) || sending.value) return

  // 仅上传图片时给默认引导语，命中 derma_image 关键词让检测直接执行
  const effectiveText = text || '请帮我检测这张图片'

  // 追加用户消息（带图片预览）
  messages.value.push({ role: 'user', content: effectiveText, imageUrl: hasImage ? previewUrl.value : null })
  input.value = ''
  scrollToBottom()

  // 追加一个空的 AI 气泡，流式期间不断追加内容
  // blocks: [{ type: 'thought', text }] 思考过程块 / [{ type: 'content', text }] 正式回复块
  const aiMsg = ref({ role: 'assistant', blocks: [] })
  messages.value.push(aiMsg.value)
  sending.value = true
  abortController = new AbortController()
  const isFirst = roundCount === 0
  roundCount++

  // 暂存已上传的图片路径，随后清理预览状态
  const currentImagePath = uploadResult.value?.path || null
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  uploadResult.value = null

  try {
    // 调用后端流式对话接口（/api 由 Vite proxy 转发到 8001）
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: abortController.signal,
      body: JSON.stringify({
        human_input: effectiveText,
        workflow_id: params.value.workflowId,
        crm: params.value.crm,
        chat_name: params.value.chatName,
        doctor_id: params.value.doctorId,
        medical_record_no: params.value.medicalRecordNo || undefined,
        restart: isFirst,
        image_path: currentImagePath,
      }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

    // 逐行解析 SSE：data: {...} / data: [DONE]
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let streamDone = false
    while (!streamDone) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 完整行以 \n\n 结束
      let idx
      while ((idx = buffer.indexOf('\n\n')) !== -1 && !streamDone) {
        const line = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        if (!line.startsWith('data:')) continue
        const data = line.slice(5).trim()
        if (data === '[DONE]') { streamDone = true; break }
        try {
          const obj = JSON.parse(data)
          if (obj && obj.content) {
            // 按事件类型分段：thought → 思考过程块（同一气泡内连排，可折叠）；
            // 无 type 的旧事件按正式回复（content）处理，向前兼容
            const t = obj.type === 'thought' ? 'thought' : 'content'
            const blocks = aiMsg.value.blocks
            const last = blocks.length ? blocks[blocks.length - 1] : null
            if (last && last.type === t) {
              last.text += obj.content
            } else {
              // shownCount：打字机动画已显示的字符数，text 为完整源文本
              blocks.push({ type: t, text: obj.content, shownCount: 0 })
            }
            // 思考过程与正式回复都启动打字机引擎（content 动画结束后正常显示）
            ensureTicker()
          }
          scrollToBottom()
        } catch { /* 忽略无法解析的行 */ }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      // Fix: 原写法 aiMsg.value.content += 写进了不存在的字段——aiMsg 只有 {role, blocks}，
      // 模板遍历的是 m.blocks，导致错误提示根本不显示。改为追加到 blocks（同类型合并）。
      const blocks = aiMsg.value.blocks
      const last = blocks.length ? blocks[blocks.length - 1] : null
      const errText = `\n[错误] ${e.message}`
      if (last && last.type === 'content') {
        last.text += errText
      } else {
        blocks.push({ type: 'content', text: errText, shownCount: 0 })
      }
      ensureTicker()
    }
  } finally {
    // 空回复提示（至少保证有一个可见块）
    if (!aiMsg.value.blocks.some((b) => b.text)) {
      aiMsg.value.blocks.push({ type: 'content', text: '（无回复）' })
    }
    sending.value = false
    abortController = null
    scrollToBottom()
  }
}

// ---------- 停止 ----------
function stopStream() {
  if (abortController) abortController.abort()
}

// ---------- 图片上传 ----------
function triggerFileInput() {
  fileInput.value?.click()
}

async function handleFileSelect(event) {
  const file = event.target.files?.[0]
  if (!file) return

  // 前端预校验
  const allowed = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp', 'image/gif']
  if (!allowed.includes(file.type)) {
    alert('仅支持 JPG/PNG/WEBP/BMP/GIF 格式的图片')
    return
  }
  if (file.size > 10 * 1024 * 1024) {
    alert('图片大小不能超过 10MB')
    return
  }

  // 预览
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = URL.createObjectURL(file)

  // 上传到后端 /api/upload
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    const resp = await fetch('/api/upload', { method: 'POST', body: formData })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || `上传失败: HTTP ${resp.status}`)
    }
    uploadResult.value = await resp.json()
  } catch (e) {
    alert(`图片上传失败: ${e.message}`)
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
    uploadResult.value = null
  } finally {
    uploading.value = false
    event.target.value = '' // 允许重复选择同一文件
  }
}

function removeImage() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  uploadResult.value = null
}

// ---------- 重置会话 ----------
function resetSession() {
  roundCount = 0
  params.value.workflowId = crypto.randomUUID()
  // 新会话覆盖本地存储：下次打开页面（或后端清理后）都从该新 id 重新建档
  localStorage.setItem('doctor_workflow_id', params.value.workflowId)
  messages.value = []
  // 清理残留的图片预览与上传结果
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  uploadResult.value = null
}

// ---------- 点击示例问题：填入并直接发送 ----------
function useSuggestion(text) {
  if (sending.value) return
  input.value = text
  sendMessage()
}
</script>

<template>
  <div class="app">
    <input
      ref="fileInput"
      type="file"
      accept="image/jpeg,image/png,image/webp,image/bmp,image/gif"
      style="display: none"
      @change="handleFileSelect"
    />
    <header class="topbar">
      <div class="brand">
        <div class="brand-logo">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
            <!-- 白色医疗十字 -->
            <path d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6V4z" fill="#fff" />
          </svg>
        </div>
        <div class="brand-text">
          <h1>数智医生智能体</h1>
          <span class="brand-sub">VVM Digital Smart Doctor · 医疗健康智能助手</span>
        </div>
      </div>

      <div class="controls">
        <!-- 会话参数与管理已常驻左侧栏，顶栏只保留常用操作 -->
        <button class="ghost" @click="resetSession" :disabled="sending">＋ 新会话</button>
      </div>
    </header>

    <div class="layout">
      <!-- 左侧栏：会话参数 + 管理常驻 -->
      <aside class="sidebar">
        <!-- 会话参数卡 -->
        <section class="side-card">
          <h3 class="side-title">会话参数<small>同一会话患者/医生 id 对应同一多轮记忆</small></h3>

          <div class="side-field">
            <label>CRM</label>
            <input v-model="params.crm" placeholder="hn" />
          </div>

          <div class="side-field">
            <label>患者</label>
            <!-- 原生下拉：默认选中「陈怡冰（1881921）」；选中其他患者后显示「姓名（病历号）」并自动回填 chatName -->
            <select v-model="params.medicalRecordNo" @change="onPatientSelect">
              <option value="">默认患者：陈怡冰（1881921）</option>
              <option
                v-for="p in allPatients"
                :key="p.medical_record_no"
                :value="p.medical_record_no"
              >{{ p.name }}（{{ p.medical_record_no }}）</option>
            </select>
            <!-- 患者列表加载 / 失败状态（不再"一直加载中"） -->
            <div v-if="!patientsLoaded && !patientsError" class="field-hint">患者列表加载中…</div>
            <div v-else-if="patientsError" class="err-box">
              <span>⚠ 患者列表加载失败：{{ patientsError }}</span>
              <button class="retry" @click="retryPatients">重试</button>
            </div>
          </div>

          <div class="side-field">
            <label>医生</label>
            <input :value="params.doctorId" disabled />
            <span class="field-hint">多 agent 模式下技能按子 agent 分配，医生固定</span>
          </div>

                  </section>

        <!-- 管理卡 -->
        <section class="side-card admin-card">
          <nav class="admin-tabs">
            <button :class="{ active: adminTab === 'agents' }" @click="adminTab = 'agents'">Agent</button>
            <button :class="{ active: adminTab === 'skills' }" @click="adminTab = 'skills'">技能</button>
          </nav>
          <WorkerSkillManager v-if="adminTab === 'agents'" :userId="params.userId" />
          <SkillManager v-else :userId="params.userId" />
        </section>
      </aside>

      <!-- 右侧主区：聊天 + 输入器 -->
      <div class="main">
        <main ref="scrollBox" class="chat">
      <div v-if="messages.length === 0 && !sending" class="empty">
        <div class="empty-logo">
          <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6V4z" fill="#fff" stroke="none" />
          </svg>
        </div>
        <h2>你好，我是智能医生助理</h2>
        <p>可以向我咨询皮肤健康、症状解读与医学常识，支持多轮对话与流式回复。</p>
        <div class="suggestions">
          <button v-for="s in suggestions" :key="s" class="suggestion" @click="useSuggestion(s)">{{ s }}</button>
        </div>
      </div>

      <div
        v-for="(m, i) in messages"
        :key="i"
        class="msg"
        :class="m.role"
      >
        <div v-if="m.role === 'assistant'" class="avatar doctor">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
            <path d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6V4z" fill="#fff" />
          </svg>
        </div>
        <div v-else class="avatar user">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </div>
        <div class="bubble">
          <img v-if="m.imageUrl" :src="m.imageUrl" class="msg-image" alt="用户图片" />
          <!-- assistant：结构化块渲染（思考过程块 + 正式回复块，同一气泡内连排）；
               user：纯文本 -->
          <template v-if="m.role === 'assistant'">
            <template v-for="(b, bi) in m.blocks" :key="bi">
              <div v-if="b.type === 'thought'" class="thought">
                <div class="thought-head">
                  <button class="thought-toggle" type="button" @click="toggleThought(b)">
                    <span class="thought-label">💡 思考过程</span>
                    <span class="thought-chevron">{{ b.collapsed ? '▸ 展开' : '▾ 收起' }}</span>
                  </button>
                  <!-- 动画进行中提供"⏩"一键跳过，立即显示全部 -->
                  <button
                    v-if="!b.collapsed && (b.shownCount ?? b.text.length) < b.text.length"
                    class="thought-skip"
                    type="button"
                    @click="b.shownCount = b.text.length"
                    title="立即显示全部"
                  >⏩</button>
                </div>
                <!-- 打字机：只渲染已播放的字符，末尾带闪烁光标 -->
                <div v-if="!b.collapsed" class="thought-body">
                  {{ (b.text || '').slice(0, b.shownCount ?? b.text.length) }}<span v-if="(b.shownCount ?? b.text.length) < b.text.length" class="type-cursor">▍</span>
                </div>
              </div>
              <div v-else class="body">{{ (b.text || '').slice(0, b.shownCount ?? (b.text || '').length) }}<span v-if="(b.shownCount ?? (b.text || '').length) < (b.text || '').length" class="type-cursor">▍</span></div>
            </template>
          </template>
          <template v-else>{{ m.content }}</template>
        </div>
      </div>
      <div v-if="sending && !messages.length" class="msg assistant">
        <div class="avatar doctor">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
            <path d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6V4z" fill="#fff" />
          </svg>
        </div>
        <div class="bubble typing" aria-label="正在输入">
          <span></span><span></span><span></span>
        </div>
      </div>
    </main>

        <footer class="composer">
      <!-- 图片预览 -->
      <div v-if="previewUrl" class="image-preview">
        <img :src="previewUrl" alt="预览" />
        <button class="remove-image" @click="removeImage" title="移除图片">×</button>
        <span v-if="uploading" class="uploading-hint">上传中...</span>
      </div>

      <!-- 图片上传按钮（用文字符号 + 代替 SVG，彻底避免 SVG 渲染问题） -->
      <button class="upload-btn" @click="triggerFileInput" :disabled="uploading" title="上传图片">+</button>

      <textarea
        v-model="input"
        rows="1"
        placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
        @keydown.enter.exact.prevent="sendMessage"
      ></textarea>
      <button
        v-if="!sending"
        @click="sendMessage"
        class="primary"
        :disabled="!input.trim() && !uploadResult"
      >发送</button>
      <button v-else @click="stopStream" class="danger">停止</button>
    </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 居中卡片式聊天窗：整体四边留白，浮在白底渐变的页面之上，滚动区含在卡片内 */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 1280px;
  margin: 0 auto;
  padding: 16px 14px;
}

/* 卡片体外壳：顶部 brand 卡 + 中部左侧栏 + 主区（右侧聊天卡），由阴影与圆角统一成卡片 */
.topbar,
.side-card,
.main {
  background: var(--panel);
}
.topbar { border-radius: var(--radius-lg) var(--radius-lg) 0 0; box-shadow: var(--shadow-md); }
.main {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column; overflow: hidden;
  border-radius: var(--radius-lg); box-shadow: var(--shadow-md);
}

/* 顶部栏 */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 22px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
  gap: 8px;
}
.brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand-logo {
  width: 42px; height: 42px; flex: none; border-radius: 12px;
  background: var(--accent-grad);
  display: flex; align-items: center; justify-content: center;
  box-shadow: var(--shadow-sm);
}
.brand-text { display: flex; flex-direction: column; min-width: 0; }
.brand-text h1 { font-size: 18px; margin: 0; line-height: 1.3; letter-spacing: .3px; }
.brand-sub { font-size: 12px; color: var(--text-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

button {
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--text-h);
  border-radius: var(--radius-sm);
  padding: 7px 15px;
  cursor: pointer;
  font-size: 14px;
  transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease, filter .12s ease;
}
button:disabled { opacity: 0.45; cursor: not-allowed; }

/* 幽灵按钮（新会话） */
.ghost { box-shadow: var(--shadow-sm); }
.ghost:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); transform: translateY(-1px); box-shadow: var(--shadow-md); }
.ghost:active:not(:disabled) { transform: translateY(0); }

/* 主按钮（发送） */
.primary {
  background: var(--accent-grad);
  border-color: transparent;
  color: #fff;
  box-shadow: var(--shadow-md);
}
.primary:hover:not(:disabled) { filter: brightness(1.06); }
.primary:active:not(:disabled) { transform: scale(.98); }

/* 停止按钮 */
.danger {
  background: linear-gradient(135deg, var(--danger), #f08080);
  border-color: transparent;
  color: #fff;
  box-shadow: var(--shadow-md);
}
.danger:hover:not(:disabled) { filter: brightness(1.06); }

/* 左侧两栏布局：sidebar + main */
.layout { flex: 1; display: flex; gap: 14px; min-height: 0; margin-top: 14px; }
.sidebar {
  width: 372px; flex: none;
  display: flex; flex-direction: column; gap: 14px;
  overflow-y: auto; padding-right: 4px;
}
.side-card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px;
  box-shadow: var(--shadow-md);
  display: flex; flex-direction: column; gap: 12px;
}
.admin-card {
  flex: 1;
  min-height: 220px;      /* 兜底最小高度：覆盖 flex 默认 min-height:auto，防止被内容撑破白框 */
  overflow-y: auto;       /* 内容超高时白框内部滚动，不再溢出白框 */
}
.side-title { font-size: 14px; font-weight: 700; color: var(--text-h); margin: 0; }
.side-title small { display: block; font-size: 11px; font-weight: 400; color: var(--text-soft); margin-top: 2px; }
.side-field { display: flex; flex-direction: column; gap: 5px; }
.side-field > label { font-size: 12px; color: var(--text); font-weight: 600; }
.side-field input,
.side-field select {
  width: 100%; padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); color: var(--text-h); font-size: 13px;
}
.side-field input:focus,
.side-field select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }
.field-hint { font-size: 11px; color: var(--text-soft); margin: 2px 0 0; line-height: 1.5; }
.err-box {
  display: flex; flex-direction: column; align-items: flex-start; gap: 6px;
  background: rgba(239, 91, 109, .08); border: 1px solid rgba(239, 91, 109, .35);
  color: var(--danger); font-size: 12px; line-height: 1.5; padding: 8px 10px; border-radius: 8px;
}
.err-box .retry { padding: 3px 12px; font-size: 12px; border-color: var(--danger); color: var(--danger); }
.muted { color: var(--text-soft); }

/* 患者下拉浮层（患者选择） */
.patient-list {
  margin-top: 4px; border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; max-height: 220px; overflow-y: auto; background: var(--panel);
  box-shadow: var(--shadow-md);
}
.patient-item {
  display: block; width: 100%; text-align: left; padding: 7px 9px;
  border: none; border-bottom: 1px solid var(--border); border-radius: 0;
  background: var(--panel); color: var(--text-h); font-size: 12px; cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.patient-item:last-child { border-bottom: none; }
.patient-item:hover { background: var(--accent-bg); color: var(--accent); }
.patient-item strong { color: var(--accent); }
.patient-empty { padding: 8px 10px; font-size: 12px; color: var(--text-soft); }

/* 管理视图（常驻左侧栏） */
.admin-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.admin-tabs button { flex: 1; padding: 8px 0; font-weight: 600; }
.admin-tabs button.active { background: var(--accent-grad); border-color: transparent; color: #fff; box-shadow: var(--shadow-sm); }

/* 消息区 */
.chat {
  flex: 1;
  overflow-y: auto;
  padding: 24px 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 空状态欢迎屏 */
.empty {
  margin: auto; text-align: center; color: var(--text);
  /* 460→560 放宽一档，配合下方 p 的 nowrap，保证欢迎语单行显示 */
  max-width: 560px; padding: 12px;
}
.empty-logo {
  width: 72px; height: 72px; margin: 0 auto 18px; border-radius: 22px;
  background: var(--accent-grad); display: flex; align-items: center; justify-content: center;
  box-shadow: var(--shadow-md);
}
.empty h2 { margin: 0 0 8px; font-size: 22px; color: var(--text-h); letter-spacing: .3px; }
.empty p { margin: 0 0 22px; font-size: 14px; line-height: 1.7; white-space: nowrap; }
.suggestions { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }
.suggestion {
  border: 1px solid var(--accent-border); background: var(--accent-bg);
  color: var(--accent); border-radius: 999px; padding: 8px 16px; font-size: 13px;
  box-shadow: var(--shadow-sm);
}
.suggestion:hover { background: var(--accent); color: #fff; border-color: transparent; transform: translateY(-2px); box-shadow: var(--shadow-md); }

/* 消息行：头像 + 气泡 */
.msg { display: flex; gap: 10px; align-items: flex-start; animation: msg-in .28s ease both; }
.msg.user { flex-direction: row-reverse; }

@keyframes msg-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}

/* 头像 */
.avatar {
  width: 40px; height: 40px; flex: none; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
}
.avatar.doctor { background: var(--accent-grad); box-shadow: var(--shadow-sm); }
.avatar.user { background: linear-gradient(135deg, #e6eef8, #dce7f3); color: #5b6b7b; }

/* 气泡 */
.bubble {
  max-width: 78%;
  padding: 11px 16px;
  border-radius: var(--radius-md);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
  font-size: 14.5px;
}
.msg.user .bubble {
  background: var(--user-grad);
  color: #fff;
  border-bottom-right-radius: 6px;
  box-shadow: var(--shadow-sm);
}
.msg.assistant .bubble {
  background: var(--panel);
  border: 1px solid var(--border);
  color: var(--text-h);
  border-bottom-left-radius: 6px;
  box-shadow: var(--shadow-md);
}
/* 医生气泡内纯文本排版增强（不做 Markdown 解析，仅 CSS 层次） */
.bubble strong { color: var(--accent); }

/* 打字指示（三点跳动） */
.typing { display: inline-flex; gap: 5px; align-items: center; padding: 15px 18px; }
.typing span {
  width: 7px; height: 7px; border-radius: 50%; background: var(--accent-2);
  animation: typing 1.2s ease-in-out infinite;
}
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }
@keyframes typing {
  0%, 60%, 100% { transform: translateY(0); opacity: .4; }
  30% { transform: translateY(-5px); opacity: 1; }
}

/* 输入区 */
.composer {
  display: flex;
  gap: 10px;
  padding: 14px 22px;
  border-top: 1px solid var(--border);
  align-items: flex-end;
}
.composer textarea {
  flex: 1;
  resize: none;
  padding: 11px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg);
  color: var(--text-h);
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  transition: box-shadow .15s ease, border-color .15s ease;
}
.composer textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }
.composer textarea::placeholder { color: var(--text-soft); }
.composer button { padding: 11px 22px; }

/* 响应式：窄屏 */
@media (max-width: 900px) {
  .layout { flex-direction: column; gap: 10px; }
  .sidebar { width: 100%; max-height: 42vh; }
  .admin-card { min-height: 0; }
  .main { min-height: 0; overflow-y: auto; }
}

@media (max-width: 600px) {
  .app { padding: 0; }
  .brand-sub { display: none; }
  .topbar, .main { border-radius: 0; }
  .bubble { max-width: 90%; }
  .chat { padding: 16px 12px; }
  .composer { padding: 12px; }
  .topbar { padding: 10px 12px; }
  .empty-logo { width: 60px; height: 60px; }
  /* 窄屏放不下单行时恢复自动换行，避免文字溢出屏幕 */
  .empty p { white-space: normal; }
}

/* 图片上传按钮：透明底 + 细边框 + 黑色加号（文字符号，不用 SVG） */
.upload-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  padding: 0;
  border: 1px solid var(--border);
  background: transparent;
  color: #1f2d3d;
  font-size: 26px;
  font-weight: 300;
  line-height: 1;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: transform .15s ease, border-color .15s ease, box-shadow .15s ease, filter .15s ease;
  flex-shrink: 0;
}
.upload-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: #1f2d3d;
  filter: brightness(1.1);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.upload-btn:active:not(:disabled) { transform: translateY(0); }
.upload-btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* 图片预览 */
.image-preview {
  position: relative;
  display: inline-block;
  padding: 6px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
.image-preview img {
  max-width: 180px;
  max-height: 120px;
  border-radius: 8px;
  display: block;
  object-fit: cover;
}
.remove-image {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--danger);
  color: #fff;
  border: 2px solid var(--panel);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-sm);
}
.remove-image:hover { filter: brightness(1.1); transform: scale(1.1); }
.uploading-hint {
  position: absolute;
  bottom: 8px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--accent);
  background: rgba(255, 255, 255, 0.9);
  padding: 2px 8px;
  border-radius: 4px;
}

/* 思考过程块：同一气泡内连排、浅色胶囊、可折叠（默认展开） */
.thought {
  background: var(--accent-bg);
  border: 1px solid var(--accent-border);
  border-radius: 8px;
  margin-bottom: 10px;
  overflow: hidden;
}
/* 头部改为布局容器（内嵌两个按钮：折叠开关 + 跳过动画） */
.thought-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  font-size: 12px;
  color: var(--accent);
}
.thought-head:hover { background: rgba(255, 255, 255, 0.55); }
.thought-toggle {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  padding: 7px 0 7px 10px;
  background: transparent;
  border: none;
  box-shadow: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--accent);
}
.thought-label { font-weight: 600; }
.thought-chevron { font-size: 11px; flex: none; }
/* 跳过动画按钮：动画进行中短暂出现，点击立即显示全部 */
.thought-skip {
  margin: 0 6px 0 0;
  padding: 2px 8px;
  font-size: 11px;
  border: 1px solid var(--accent-border);
  border-radius: 6px;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  flex: none;
}
.thought-skip:hover { background: var(--accent); color: #fff; }
.thought-body {
  padding: 0 10px 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-soft);
  white-space: pre-wrap;
  word-break: break-word;
}
/* 打字机光标：动画播放中闪烁，直观表明"正在输出" */
.type-cursor {
  color: var(--accent);
  animation: type-blink 1s steps(1) infinite;
}
@keyframes type-blink {
  50% { opacity: 0; }
}

/* 用户消息中的图片 */
.msg-image {
  max-width: 220px;
  max-height: 160px;
  border-radius: 8px;
  margin-bottom: 6px;
  display: block;
  object-fit: cover;
}
</style>
