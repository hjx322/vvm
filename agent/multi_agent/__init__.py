"""多 Agent（主管-子 agent）改造包（计划 P1-P6 → agent 化统一命名）

职责划分：
  - schemas        : SupervisorPlan / AgentTask / AgentResult / SafetyVerdict 结构化协议
  - base_worker    : 子 agent 基类（独立 system prompt / 上下文 / 模型分级）
  - skill_executor : 薄壳，复用 UnifiedSkillDispatcher 内核执行技能
  - agent_registry : 子 agent 注册表
  - workers        : 具体子 agent（patient_knowledge/search/imaging/general 数据 agent …）
  - reasoning_workers: L3 reasoning agent（safety 安全门卫）
  - supervisor     : Supervisor（规划 + 综合）
  - orchestrator   : 图编排器（作为主图 node_multi_agent 节点）
  - prompts        : 各角色 system prompt

设计约定：
  子 agent 不建独立 StateGraph、不引新框架；Supervisor 与子 agent 是
  "类 + 结构化协议"关系，LangGraph 运行时仍是"同一时刻一个节点"，
  由 orchestrator 在节点内部用 asyncio.gather 并行调度 L1 数据子 agent。
  后续若升级真子图，只需把 asyncio.gather 换成 Send API fan-out，state 字段不变。
"""