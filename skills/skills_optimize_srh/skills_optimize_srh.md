# 技能调度架构优化说明

> 2026/05，参考 Hermes Agent、CoPaw、QClaw 的设计，对技能匹配、执行、隔离三个环节做了重构。

---

## 一、原有问题

### 1. 自定义技能执行路径不可靠

`skills/registry.py` 中 `SKILL_REGISTRY` 硬编码了 4 个内置技能
（mysql_query、milvus_query、web_search、derma_image）。所有用户上传的自定义技能（healthfit、weather-cn 等）走 `_execute_external_skill`，即 LLM 工具循环——LLM 读取 SKILL.md 后自行决定调用哪个脚本、传什么参数。这种方式依赖 LLM 的指令理解能力，可能出现调用错误的脚本、传参偏差，或陷入多轮循环。

### 2. 延迟偏高

以一个天气查询为例，完整链路是：LLM 选择技能 → 读取技能文档 → 决定调哪个工具 → 执行脚本 → LLM 汇总结果。整个过程涉及 2-11 次 LLM 调用，总耗时 12-55 秒。

### 3. 租户隔离不足

- `get_enabled_skills()` 查询时未过滤 `skill.user_id`，用户 B 可以在数据库层面关联到用户 A 的技能记录
- 自定义脚本执行时没有限制工作目录，脚本可以访问其他用户的数据
- 技能存储路径使用 `medical_record_no`（病历号）作为隔离键，同一用户在不同 patient 间切换时技能目录不一致

### 4. 依赖 Node.js 生态

`skills_prompt.py` 通过 `npx openskills list` 获取技能列表，`import_skill.py` 依赖 `clawhub install` 和 `npx openskills update`。项目主体是 Python，增加了一套 Node 运行时依赖。

---

## 二、参考框架

- **Hermes Agent**：渐进式技能加载（Progressive Disclosure），启动时只加载 name + description，匹配时用 FTS5 全文检索筛选候选技能，执行时才注入完整 SKILL.md。技能选择 prompt 从 ~4500 tokens 缩减到 ~200 tokens。
- **CoPaw（阿里）**：标准化技能执行协议，技能目录内通过声明文件描述 entrypoint、input/output schema 和 runner 类型，使自定义技能可以跳过 LLM 工具循环直接执行。
- **QClaw（腾讯）**：三层安全模型——Prompt 层防注入、Skills 层校验来源和权限、Script 层沙箱隔离，每个 Agent 有独立的权限边界。

---

## 三、总体架构

三个主要改动：3-tier 技能匹配替代单一 LLM 调用；DynamicSkillRegistry 统一路由替代分支判断；基于 user_id 的三层租户隔离。

```
用户输入
  │
  ▼
Phase 1 — 技能匹配
  ├─ Tier 1: 关键词规则          ~0ms
  ├─ Tier 2: SQLite FTS5 全文检索 ~1-10ms（中文 2-gram）
  └─ Tier 3: LLM 结构化输出      ~2-5s（降级）
  │
  ▼
Phase 2 — DynamicSkillRegistry 路由
  ├─ python_handler     → Python 类直接调用     1-3s
  ├─ subprocess_script  → subprocess.run()      0.5-2s
  └─ llm_tool_loop      → LLM + Tools 循环      5-30s（降级保留）
  │
  ▼
Phase 3 — 租户权限校验
  ├─ user_id 所有权验证
  ├─ 执行目录约束
  └─ 最小化环境变量
```

自定义技能的执行路径从 LLM 工具循环变为直接 subprocess 执行。依赖 `execution.yaml` 声明协议——调度器读取 runner 类型和 entrypoint 后直接调用脚本，不再需要 LLM 在中间做推理。

---

## 四、核心模块

### 4.1 DynamicSkillRegistry

`skills/dynamic_registry.py`

替代硬编码的 `SKILL_REGISTRY`，支持运行时注册和注销。内部维护两个字典：

- `_handlers`：Python handler 实例，用于内置技能
- `_manifests`：SkillManifest 对象，描述自定义技能的入口和执行参数

首次调用 `dispatch()` 时自动扫描 `user_skills/{user_id}/` 目录，读取 execution.yaml 或 SKILL.md 完成注册，无需重启。

### 4.2 SkillManifest

`skills/manifest.py`

每个自定义技能可以包含一个 execution.yaml，声明执行方式：

```yaml
name: clawhub_weather-cn
description: "天气查询"
runner: subprocess_script
entrypoint: weather-cn.sh
timeout: 30
keywords: [天气, 气温, 下雨]
```

`runner` 字段决定执行路径：

| runner | 执行方式 | 延迟 | 适用场景 |
|--------|----------|------|----------|
| `python_handler` | 加载 Python 类直接 invoke | 1-3s | 需要访问 MCP 等复杂逻辑 |
| `subprocess_script` | `subprocess.run()` | 0.5-2s | Shell/Python 脚本，大多数自定义技能 |
| `llm_tool_loop` | LLM 工具循环 | 5-30s | 需要多步推理，降级保留 |

如果技能目录内没有 execution.yaml，系统会尝试从 SKILL.md 的 YAML frontmatter 推断，再尝试自动扫描 scripts/ 目录发现入口脚本。都失败时才降级到 LLM 工具循环。

### 4.3 3-tier 技能匹配

`DynamicSkillRegistry.match()` 中实现：

- **Tier 1**：关键词规则。维护 keyword → skill 映射表，用户输入中匹配到关键词（如"天气""健身""图片"）直接命中，0ms 出结果
- **Tier 2**：SQLite FTS5 全文检索。为每个技能建立"可检索文档"（name + description + keywords + triggers + SKILL.md 正文），中文先按 2-gram 预处理再以 unicode61 建索引；用户输入拆 2-gram 逐词命中计数、按命中比例排序召回 top-N。覆盖 Tier 1 关键词表漏掉的长句 / 变体表述（如"复诊记录"→ 命中"就诊记录"）
- **Tier 3**：LLM 降级。前两层未命中时，用 Pydantic 结构化输出让 LLM 选择

常见查询（查病历、查天气、健身建议、皮肤病检测等）的 80% 以上在 Tier 1 命中，不触发 LLM 调用。

### 4.4 UnifiedSkillDispatcher

`skills/skills_optimize_srh/skill_dispatcher.py`

调度流程：

1. 从 state 取 human_input 和 user_id，调用 registry.match()
2. 未命中时降级到 LLM 结构化输出（`SkillSelectionResult`，Pydantic 模型）
3. 命中后并行 `asyncio.gather` 执行
4. 合并结果写入 state.sub_agent_input，交下游 chat node 使用

结构化输出替代了原先的正则匹配方式（从 LLM 文本中正则提取 `openskills read xxx`），不再依赖 LLM 输出特定格式。

### 4.5 移除 OpenSkill / Node.js 依赖

| 原有调用 | 替换为 |
|----------|--------|
| `npx openskills list` | MySQL 查询 + DynamicSkillRegistry |
| `npx openskills read <skill>` | `open()` 读取本地文件 |
| `clawhub install` + `npx openskills update` | `SkillManager.upload_skill()` |

---

## 五、租户隔离

### Layer 1 — 存储

- 技能目录从 `user_skills/{medical_record_no}/` 改为 `user_skills/{user_id}/`
- Skill 表：`user_id = 'system'` 为内置技能（所有用户只读共享），`user_id` 为实际值的是用户私有技能

### Layer 2 — 权限

- `enable_skill()`：验证 agent 属于该 user，且 skill 的 user_id 匹配或为 builtin
- `get_enabled_skills()`：JOIN Skill 表时加 `Skill.user_id == owner_user_id OR Skill.is_builtin == True`

### Layer 3 — 执行

- 执行目录限制在 `user_skills/{user_id}/` 下
- 执行前校验实际路径不超出沙箱根目录
- 环境变量只传 PATH，不泄露宿主环境信息

---

## 六、性能对比

以天气查询（clawhub_weather-cn）为例：

| 环节 | 优化前 | 优化后 |
|------|--------|--------|
| 技能选择 | LLM 调用 2-5s | 关键词匹配 0ms |
| 读取技能文档 | npx 子进程 2-5s | 本地文件 <1ms |
| 执行 | LLM 工具循环 10-50s | subprocess 跑脚本 0.5-2s |
| 总延迟 | 12-55s | 0.5-2s |

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 自定义技能执行延迟 | 12-55s | 0.5-3s |
| 技能选择触发 LLM | 每次 | 80%+ 跳过 |
| 租户隔离 | 存在漏洞 | 三层隔离 |
| 新技能接入 | 编写 Python Handler 类 | 编写 execution.yaml + 脚本 |
| Node.js 依赖 | 需要 | 不需要 |
| 技能热加载 | 不支持 | 首次调用时自动扫描 |

---

## 七、变更文件

### 新增

| 文件 | 说明 |
|------|------|
| `skills/dynamic_registry.py` | 动态技能注册表 |
| `skills/manifest.py` | SkillManifest 数据类 + execution.yaml 解析 |
| `skills/skills_optimize_srh/skill_dispatcher.py` | 统一技能调度器（3-tier 匹配 + 统一路由） |
| `user_skills/.../clawhub_weather-cn/current/execution.yaml` | 天气查询技能执行协议 |
| `user_skills/.../healthfit/current/execution.yaml` | 健康管理技能执行协议 |

### 修改

| 文件 | 改动 |
|------|------|
| `skills/__init__.py` | 导出 DynamicSkillRegistry、SkillManifest |
| `agent/core/state.py` | 新增 `user_id` 字段 |
| `agent/digital_smart_doctor_agent.py` | `_init_state` 初始化 `user_id` |
| `agent/nodes/pre_process_node.py` | 从 chat_name 提取 `user_id` |
| `agent/tools/skill_tools.py` | 最小化环境变量、三级路径校验 |
| `backend/services/agent_skill_manager.py` | `get_enabled_skills()` 加租户过滤；`enable_skill()` 加所有权校验 |
| `backend/services/skill_manager.py` | `get_skill_detail()` 加租户权限校验 |
| `prompt/skills_prompt.py` | 移除 `npx openskills list`，改为 DB 查询 |
| `import_skill.py` | 移除 `clawhub install` 和 `npx openskills update` |

### 文件整理

以下 14 个与技能优化相关的根目录文件已迁移到 `skills/skills_optimize_srh/`：

`debug_db.py`、`debug_skills.py`、`demo_skill_permission.py`、`enable_healthfit.py`、`import_skill.py`、`list_skills.py`、`manage_skills.py`、`register_healthfit.py`、`skill_dispatcher.py`、`test_skill_call.py`、`test_agent_manager.py`、`base.py`、`manifest.py`、`dynamic_registry.py`

迁移后所有受影响模块的 import 路径已同步更新，项目正常运行不受影响。

---

## 八、文件说明

### 核心模块

**`base.py`** — Skill 基类和公共数据结构

定义了技能系统的三个基础类型：
- `SkillResult`：技能执行结果 dataclass（success、content、raw）
- `NecessaryDataResult`：必要数据准备结果 dataclass（success、content）
- `SkillHandler`：所有技能的抽象基类，声明 `prepare_necessary_data`、`call`、`execute_with_llm` 三个必须实现的方法，同时提供对应的异步版本（`prepare_necessary_data_async`、`call_async`、`execute_with_llm_async`），默认实现在 executor 中运行同步方法

**`manifest.py`** — SkillManifest 数据类与 execution.yaml 解析

定义了 `SkillManifest` dataclass 和 `RunnerType` 类型别名。核心能力：
- `from_yaml(yaml_path)`：从 execution.yaml 文件加载技能执行清单
- `from_skill_md(md_path)`：从 SKILL.md 的 YAML frontmatter 提取清单（兼容没有 execution.yaml 的旧格式技能）
- `to_dict()`：序列化为字典

SkillManifest 字段包括 name、description、version、runner（执行方式）、entrypoint（入口脚本）、timeout（超时）、keywords（触发关键词）、base_dir（技能根目录）等。

**`dynamic_registry.py`** — 动态技能注册表

替代硬编码 `SKILL_REGISTRY`，实现：
- 运行时注册/注销技能（`register_builtin`、`register_custom`、`unregister`）
- 3-tier 技能匹配（`match` 方法：关键词规则 → SQLite FTS5 全文检索 → 返回空让上层降级 LLM）
- 统一执行路由（`execute` 方法：根据 manifest.runner 类型分发到 `_execute_handler`、`_execute_script` 或 `_execute_llm_loop`）
- 内置关键词映射表 `_BUILTIN_KEYWORD_MAP`，覆盖 mysql_query、milvus_query、web_search、derma_image 四个内置技能的关键词
- `get_all_available(user_id)`：列出某用户可见的所有技能（内置 + 自定义）
- `get_builtin_names()` / `get_custom_names(user_id)`：按类别获取技能名称列表

**`skill_dispatcher.py`** — 统一技能调度器

`UnifiedSkillDispatcher` 类，技能调度的主入口：
- `dispatch(state)`：主调度流程，替代原 `SkillQueryNode.execute_async()`，返回格式兼容
- `select_skills(human_input, state)`：LLM 结构化输出选择技能（Tier 3 降级），使用 Pydantic `SkillSelectionResult` 模型
- `_execute_single_skill(skill_name, state, human_input)`：单技能执行，含超时控制（默认 300s）
- `_execute_skill_core(...)`：执行路由核心——内置技能调 handler、有 manifest 的调 script、都失败则降级 LLM 工具循环
- `_execute_internal_skill(...)`：委托给 DynamicSkillRegistry 执行内置技能
- `_execute_custom_script(...)`：直接 subprocess 执行自定义技能脚本（从 execution.yaml / SKILL.md 读取 entrypoint）
- `_execute_external_skill(...)`：LLM 工具循环执行（降级方案，最多 10 轮）
- `_ensure_user_skills_loaded(user_id)`：按需扫描 `user_skills/{user_id}/` 目录并注册到 registry
- `SKILL_SELECTION_SYSTEM_PROMPT`：调度器使用的系统提示词常量

### 工具脚本

**`manage_skills.py`** — 技能权限管理 CLI 工具

命令行工具，基于 argparse，支持子命令：
- `list <agent_id> <user_id>`：列出医生所有技能（启用 + 禁用）
- `enable <agent_id> <skill_id> <user_id>`：为医生启用技能
- `disable <agent_id> <skill_id> <user_id>`：为医生禁用技能
- `batch-enable <agent_id> <user_id> <skills...>`：批量启用

直接操作数据库，通过 `AgentManager` 和 `AgentSkillManager` 完成增删改查。

**`import_skill.py`** — 技能 ZIP 导入工具

从本地 ZIP 包导入自定义技能。流程：
1. 解压 ZIP 到临时目录
2. 检测并处理嵌套目录
3. 检查 SKILL.md 是否存在（警告但不阻断）
4. 移动到 `.claude/skills/{prefix}{skill_name}/`
5. 清理临时文件

使用方式：`python import_skill.py --upload <zip_path> [skill_name]`

已废弃 `clawhub install` 和 `npx openskills update` 调用链。

**`enable_healthfit.py`** — 为指定医生启用 healthfit 技能

一次性脚本，直接创建数据库连接，调用 `AgentSkillManager.enable_skill()` 为 agent `agt_d75e25a434fa457f`、用户 `1827196` 启用 healthfit。

**`register_healthfit.py`** — 注册 healthfit 技能到数据库

检查 healthfit 是否已存在于 Skill 表，不存在则创建记录（skill_id、user_id、description、is_builtin、current_path），然后调用 `AgentSkillManager.enable_skill()` 为医生启用。

**`list_skills.py`** — 查询数据库中所有技能

直接通过 SQLAlchemy 查询 Skill 表，打印每个技能的 skill_id 和 description。

### 调试与测试

**`debug_db.py`** — 数据库技能配置调试

查询指定医生（agent_id）的详细信息，打印 agent 基本信息和启用的技能列表。用于验证数据库中的技能配置是否正确。

**`debug_skills.py`** — 技能列表描述调试

调用 `get_skills_description(user_id, doctor_id)` 获取启用的技能描述文本，打印结果。用于调试技能描述生成逻辑。

**`demo_skill_permission.py`** — 技能权限管理演示脚本

完整演示医生技能权限管理的五个步骤：
1. 查看医生当前技能状态
2. 禁用 healthfit 技能
3. 查看更新后的状态
4. 重新启用 healthfit 技能
5. 查看最终状态

直接操作数据库，用于验证 `AgentSkillManager` 的启用/禁用功能是否正常。

**`test_skill_call.py`** — 技能调用集成测试

异步脚本，创建 `DigitalSmartDoctorAgent` 实例，以流式方式测试技能调用（默认测试输入："帮我制定一个健身训练计划"，触发 healthfit 技能）。验证从 Agent 初始化到技能调度再到流式输出的完整链路。

**`test_agent_manager.py`** — 多用户多医生技能管理集成测试

`TestSkillAndAgentManager` 类，按顺序执行七个测试步骤：
1. 为用户创建自定义技能（从 ZIP 上传）
2. 为用户创建医生
3. 医生启用自定义技能
4. 医生启用系统内置技能
5. 查看医生技能详情
6. 关闭自定义技能
7. 删除自定义技能 → 删除医生 → 验证级联删除

每个步骤包含断言验证，测试覆盖技能和医生的完整生命周期。

### 包初始化

**`__init__.py`** — 使 `skills_optimize_srh` 成为 Python package

---

## 九、接入方式

在 `agent/digital_smart_doctor_agent.py` 中将 `SkillQueryNode` 替换为 `UnifiedSkillDispatcher`：

```python
from skill_dispatcher import UnifiedSkillDispatcher

dispatcher = UnifiedSkillDispatcher(llm=self.llm)
builder.add_node("node_query_skills", dispatcher.dispatch)
```

不需要安装 npx / Node.js，不修改 skills/registry.py。如果出问题，恢复为原来的 `SkillQueryNode` 即可。

---

## 十、待完成

- Tier 2 FTS5 全文索引已接入（内存 SQLite，内置 + 用户技能各建一条文档，2-gram 中文分词，~1-10ms）；当前为惰性重建，技能热注册后下次 match 自动重建
- `DynamicSkillRegistry` 的热加载基于文件系统扫描，没有文件监听机制，新上传的技能在下一次 dispatch 调用时才会被发现
- 执行沙箱在 Windows 上未做 token 降权（Linux 上可以降为 `nobody`），当前依赖路径校验和最小环境变量
- `llm_tool_loop` runner 的技能仍走较慢的 LLM 工具循环路径，后续可考虑迁移为 `subprocess_script`
