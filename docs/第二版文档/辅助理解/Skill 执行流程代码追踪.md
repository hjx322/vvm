# Skill 执行流程代码追踪

## 目录
1. [LLM 意图识别流程](#llm-意图识别流程)
2. [内部Skill执行详细步骤](#内部skill执行详细步骤)
3. [外部Skill执行详细步骤](#外部skill执行详细步骤)
4. [错误恢复流程](#错误恢复流程)
5. [关键代码片段](#关键代码片段)

---

## LLM 意图识别流程

### 调用链路

```
ChatNode / RoutingNode
    │
    └─→ user_input: "我是谁，查下北京天气"
        │
        ▼
    SkillQueryNode.execute_async()
        │ [agent/nodes/skill_query_node.py:531]
        │
        ├─ 获取: human_input = state["human_input"]
        │
        └─→ _extract_skill_names_from_llm(human_input, state)
            │ [skill_query_node.py:454]
            │
            ├─1. get_skills_system_prompt(state)
            │    │ [prompt/skills_prompt.py:67]
            │    │
            │    ├─ 调用 get_skills_description(user_id, doctor_id)
            │    │  │ [prompt/skills_prompt.py:32]
            │    │  │
            │    │  ├─ 连接MySQL
            │    │  ├─ 查询: AgentManager.get_agent_details()
            │    │  │       → 获取该医生启用的skills列表
            │    │  │
            │    │  └─ 返回:
            │    │     "启用的技能:
            │    │      1.mysql_query: 患者个人信息查询
            │    │      2.milvus_query: 医学知识查询
            │    │      3.clawhub_weather-cn: 天气查询"
            │    │
            │    └─ 组织成完整的系统提示词
            │       内容包括: 工具清单、决策规则、输出格式、示例
            │
            ├─2. 构造messages
            │    messages = [
            │        SystemMessage(content=skills_system_prompt),
            │        HumanMessage(content=human_input)
            │    ]
            │
            ├─3. LLM调用
            │    response = await self.llm.ainvoke(messages)
            │    │
            │    │ LLM分析:
            │    │ - "我是谁" → mysql_query (患者信息)
            │    │ - "北京天气" → clawhub_weather-cn (天气)
            │    │
            │    └─ 输出:
            │       "openskills read mysql_query
            │        openskills read clawhub_weather-cn"
            │
            ├─4. 检查是否有openskills命令
            │    if "openskills read" not in response_content:
            │        return []  # 没有技能调用
            │
            └─5. 提取技能名称
                 skill_names = extract_skill_names(response_content)
                 │
                 │ 正则表达式: r'openskills\s+read\s+(\S+)'
                 │
                 └─ 返回: ["mysql_query", "clawhub_weather-cn"]
```

### get_skills_description() 的数据库查询

```python
# prompt/skills_prompt.py:32-61

def get_skills_description(user_id, doctor_id):
    # 1. 连接医生管理数据库
    db_url = f"mysql+pymysql://.../{mysql_config.db}"
    engine = create_engine(db_url, pool_recycle=3600, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = SessionLocal()

    # 2. 使用AgentManager查询医生详情
    manager = AgentManager(db_session)
    result = manager.get_agent_details(user_id=user_id, agent_id=doctor_id)
    #
    # 查询逻辑:
    # SELECT * FROM agents WHERE agent_id = doctor_id AND user_id = user_id
    # SELECT skill_id, description FROM agent_skills
    #   WHERE agent_id = doctor_id
    #   AND enabled = true
    #

    # 3. 组织输出
    skill_prompt = "\n启用的技能:"
    if result["enabled_skills"]:
        for i, skill in enumerate(result["enabled_skills"]):
            skill_prompt += f"{i+1}.{skill['skill_id']}: {skill['description']}\n"

    return skill_prompt
    #
    # 返回示例:
    # "启用的技能:
    #  1.mysql_query: 患者个人信息查询、历史就诊记录
    #  2.milvus_query: 医学知识库查询
    #  3.clawhub_weather-cn: 天气预报查询"
```

---

## 内部Skill执行详细步骤

### 调用链路

```
extract_skill_names() → ["mysql_query", "clawhub_weather-cn", ...]
    │
    ▼
SkillQueryNode.execute_async() [继续]
    │
    ├─ skill_names = ["mysql_query", "clawhub_weather-cn"]
    │
    ├─ 第1步: 并行执行所有skill
    │  tasks = [
    │      _execute_single_skill_async("mysql_query", state, human_input),
    │      _execute_single_skill_async("clawhub_weather-cn", state, human_input)
    │  ]
    │  results = await asyncio.gather(*tasks, return_exceptions=True)
    │  │ [skill_query_node.py:558-559]
    │  │
    │  └─→ 对每个task，执行 _execute_single_skill_async()
    │
    └─ 第2步: 等待所有task完成
```

### _execute_single_skill_async("mysql_query") 详细步骤

```
_execute_single_skill_async("mysql_query", state, human_input)
    │ [skill_query_node.py:323-353]
    │
    └─→ asyncio.wait_for(
        _execute_skill_with_retry("mysql_query", state, human_input),
        timeout=300.0  # skill_timeout
    )
        │
        └─→ _execute_skill_with_retry() [skill_query_node.py:355-452]
            │
            ├─ 第1步: 检测是否为外部技能
            │  if skill_name not in SKILL_REGISTRY:  # "mysql_query" in registry
            │      # 走外部技能流程
            │  else:
            │      # 走内部技能流程 ✓
            │
            ├─ 第2步: 获取handler和准备必要数据
            │  handler = SKILL_REGISTRY["mysql_query"]  # MySQLQuerySkill()
            │  │
            │  │ 并行执行:
            │  └─→ asyncio.gather(
            │      _run_npx_openskills_read_async("mysql_query"),
            │      handler.prepare_necessary_data_async(state),
            │      return_exceptions=True
            │  )
            │      │
            │      ├─ 分支A: npx openskills read
            │      │  │
            │      │  └─→ _run_npx_openskills_read_async() [skill_query_node.py:45-70]
            │      │      npx = shutil.which("npx") or shutil.which("npx.cmd")
            │      │      proc = await asyncio.create_subprocess_exec(
            │      │          npx, "-y", "openskills", "read", "mysql_query",
            │      │          stdout=asyncio.subprocess.PIPE,
            │      │          stderr=asyncio.subprocess.PIPE
            │      │      )
            │      │      stdout, stderr = await proc.communicate()
            │      │      return stdout.decode('utf-8')
            │      │      │
            │      │      │ 返回SKILL.md内容 (如果在OpenSkills hub注册了)
            │      │      │ 或返回空字符串 (本地不存在或注册失败)
            │      │      │
            │      │      └─ skill_content = ""  # 对于内置skill通常为空
            │      │
            │      └─ 分支B: 准备必要数据
            │         │
            │         └─→ MySQLQuerySkill.prepare_necessary_data_async(state)
            │             │ [base.py:49-56]
            │             │
            │             └─→ executor中执行同步版本
            │                 │
            │                 └─→ MySQLQuerySkill.prepare_necessary_data(state)
            │                     │ [mysql_query_skill.py:38-49]
            │                     │
            │                     ├─ if "medical_record_no" not in state:
            │                     │      return NecessaryDataResult(False, "MySQL 查询缺少病历号")
            │                     │
            │                     ├─ if "crm" not in state:
            │                     │      return NecessaryDataResult(False, "MySQL 查询缺少CRM参数")
            │                     │
            │                     └─ return NecessaryDataResult(
            │                            True,
            │                            f"病历号/medical_record_no:{state['medical_record_no']}\
            │                             crm:{state['crm']}"
            │                        )
            │
            │                     # 假设state中有数据:
            │                     # necessary_data = NecessaryDataResult(
            │                     #     success=True,
            │                     #     content="病历号: 1001\ncrm: hn_db"
            │                     # )
            │
            ├─ 第3步: 检查必要数据是否完整
            │  if not necessary_data.success:
            │      return {
            │          "skill_name": "mysql_query",
            │          "success": False,
            │          "content": necessary_data.content,  # 错误信息
            │          "silent": True  # 不在输出中显示
            │      }
            │  else:
            │      # 继续执行 ✓
            │
            ├─ 第4步: 构造LLM输入消息
            │  messages = [
            │      SystemMessage(content=f"Skill content:\n{skill_content},
            │                                Necessary data:\n{necessary_data.content}"),
            │      HumanMessage(content=human_input)
            │  ]
            │  │
            │  │ 例如:
            │  │ SystemMessage: "Skill content:
            │  │                  (SKILL.md内容，通常为空)
            │  │
            │  │                  Necessary data:
            │  │                  病历号: 1001
            │  │                  crm: hn_db"
            │  │
            │  │ HumanMessage: "我是谁"
            │
            ├─ 第5步: 使用LLM生成结构化参数并执行Skill
            │  search_response = await handler.execute_with_llm_async(self.llm, messages)
            │  │ [mysql_query_skill.py:18-36]
            │  │
            │  └─→ MySQLQuerySkill.execute_with_llm_async(llm, messages)
            │      │
            │      ├─ 生成结构化LLM
            │      │  structured_llm = llm.with_structured_output(MySQLQuerySchema)
            │      │  │
            │      │  │ MySQLQuerySchema定义 [schemas.py]:
            │      │  │ class MySQLQuerySchema(BaseModel):
            │      │  │     medical_record_no: str = Field(...)
            │      │  │     crm: str = Field(...)
            │      │  │     db_name: str = Field(default="medical")
            │      │  │     table: str = Field(...)
            │      │  │     where_clause: str = Field(...)
            │      │  │
            │      │  │ LLM根据schema生成JSON:
            │      │  │ {
            │      │  │   "medical_record_no": "1001",
            │      │  │   "crm": "hn_db",
            │      │  │   "db_name": "medical",
            │      │  │   "table": "patient_info",
            │      │  │   "where_clause": "id = 1001"
            │      │  │ }
            │      │
            │      ├─ 调用LLM
            │      │  params_obj = await structured_llm.ainvoke(messages)
            │      │  # params_obj 是 MySQLQuerySchema 的实例
            │      │
            │      └─ 执行Skill
            │         return await self.call_async(params_obj.model_dump_json())
            │         │
            │         └─→ MySQLQuerySkill.call_async() [base.py:58-65]
            │             │
            │             └─→ executor中执行同步版本
            │                 │
            │                 └─→ MySQLQuerySkill.call(input_param)
            │                     │ [mysql_query_skill.py:51-68]
            │                     │
            │                     ├─ 提取JSON部分
            │                     │  start = input_param.find("{")
            │                     │  content = search_with_retry(input_param[start:], max_retries=3)
            │                     │  │
            │                     │  └─→ search_with_retry()
            │                     │      │ [.claude/skills/mysql_query/scripts/search.py]
            │                     │      │
            │                     │      ├─ 重试逻辑(自动3次):
            │                     │      │  for attempt in range(1, max_retries + 1):
            │                     │      │      try:
            │                     │      │          # 执行SQL查询
            │                     │      │          session = get_patient_mysql(crm)
            │                     │      │          query = session.query(table)
            │                     │      │                         .filter(where_clause)
            │                     │      │          result = query.all()
            │                     │      │          return json.dumps(result, ensure_ascii=False)
            │                     │      │      except Exception as e:
            │                     │      │          if "MySQL server has gone away" in str(e):
            │                     │      │              # 重新连接
            │                     │      │              session = _reconnect()
            │                     │      │          else:
            │                     │      │              raise
            │                     │      │
            │                     │      └─ 返回: JSON格式的查询结果
            │                     │         "[{id:1001, name:张三, ...}]"
            │                     │
            │                     └─ return SkillResult(
            │                            success=True,
            │                            content=content
            │                        )
            │
            ├─ 第6步: 重试机制 (如果第一次失败)
            │  retry_count = 0
            │  while not search_response.success and retry_count < 3:
            │      │ [skill_query_node.py:429-438]
            │      │
            │      ├─ 添加错误恢复提示词
            │      │  messages.append(
            │      │      SystemMessage(content=PROMPT_QUERY_ERROR_RETRY.format(
            │      │          error_content=search_response.content
            │      │      ))
            │      │  )
            │      │  │
            │      │  │ PROMPT_QUERY_ERROR_RETRY内容:
            │      │  │ "前一次尝试出现错误: {error_content}
            │      │  │  请尝试调整参数或查询方式。"
            │      │
            │      ├─ 重新调用LLM
            │      │  retry_response = await self.llm.ainvoke(messages)
            │      │  input_param = retry_response.content
            │      │
            │      ├─ 重新执行Skill
            │      │  search_response = await handler.call_async(str(input_param))
            │      │
            │      └─ retry_count += 1
            │
            ├─ 第7步: 返回结果
            │  return {
            │      "skill_name": "mysql_query",
            │      "success": search_response.success,
            │      "content": search_response.content
            │  }
            │  │
            │  │ 返回示例 (成功):
            │  │ {
            │  │     "skill_name": "mysql_query",
            │  │     "success": True,
            │  │     "content": "[{id:1001, name:张三, phone:13800138000, ...}]"
            │  │ }
            │  │
            │  │ 返回示例 (失败):
            │  │ {
            │  │     "skill_name": "mysql_query",
            │  │     "success": False,
            │  │     "content": "MySQL 查询失败: Connection timeout"
            │  │ }
            │
            └─ (end of _execute_skill_with_retry)
```

---

## 外部Skill执行详细步骤

### _execute_external_skill_async("clawhub_weather-cn") 详细步骤

```
_execute_skill_with_retry("clawhub_weather-cn", state, human_input)
    │ [skill_query_node.py:355-452]
    │
    ├─ 第1步: 检测是否为外部技能
    │  if skill_name not in SKILL_REGISTRY:  # "clawhub_weather-cn" 不在
    │      return await _execute_external_skill_async(skill_name, human_input, state)
    │
    └─→ _execute_external_skill_async("clawhub_weather-cn", human_input, state)
        │ [skill_query_node.py:72-128]
        │
        ├─ 第1步: 读取SKILL.md文档
        │  skill_doc = await _get_external_skill_documentation("clawhub_weather-cn", state)
        │  │ [skill_query_node.py:130-162]
        │  │
        │  ├─ 检查缓存
        │  │  if "clawhub_weather-cn" in self._external_skill_docs_cache:
        │  │      return cached_content
        │  │
        │  ├─ 构造SKILL.md路径
        │  │  skill_path = os.path.join(
        │  │      "./user_skills",
        │  │      state['medical_record_no'],     # "1001"
        │  │      "clawhub_weather-cn",
        │  │      "current",
        │  │      "SKILL.md"
        │  │  )
        │  │  # 完整路径: ./user_skills/1001/clawhub_weather-cn/current/SKILL.md
        │  │
        │  ├─ 检查文件是否存在
        │  │  if not os.path.exists(skill_path):
        │  │      logger.error(f"SKILL.md文件不存在: {skill_path}")
        │  │      return None
        │  │
        │  ├─ 读取文件内容
        │  │  with open(skill_path, 'r', encoding='utf-8') as f:
        │  │      content = f.read()
        │  │
        │  ├─ 缓存内容
        │  │  self._external_skill_docs_cache["clawhub_weather-cn"] = content
        │  │
        │  └─ 返回内容
        │     # SKILL.md示例内容:
        │     # ---
        │     # name: clawhub_weather-cn
        │     # description: 查询中国城市天气
        │     # ---
        │     #
        │     # ## Usage
        │     # 命令: ./weather-cn.sh <city_name>
        │     # 参数: city_name (城市名称，如"北京"、"上海")
        │     # 输出: JSON格式的天气信息
        │     # {
        │     #   "city": "北京",
        │     #   "temp": "15/2℃",
        │     #   "weather": "晴",
        │     #   "wind": "北风2级"
        │     # }
        │
        ├─ 第2步: LLM生成执行命令
        │  raw_command = await _generate_external_skill_command(
        │      "clawhub_weather-cn",
        │      skill_doc,
        │      human_input="我要查下北京天气",
        │      retry_count=0
        │  )
        │  │ [skill_query_node.py:164-211]
        │  │
        │  ├─ 构造系统提示词
        │  │  system_content = get_external_skill_system_prompt(
        │  │      "clawhub_weather-cn",
        │  │      skill_doc
        │  │  )
        │  │  │ [external_skill_prompt.py]
        │  │  │
        │  │  │ 提示词内容:
        │  │  │ "你是一个技能执行器。
        │  │  │  根据以下SKILL.md文档，生成要执行的命令。
        │  │  │
        │  │  │  SKILL.md:
        │  │  │  {skill_doc内容}
        │  │  │
        │  │  │  只输出命令，不要有其他解释。"
        │  │
        │  ├─ 构造用户提示词
        │  │  user_content = get_external_skill_user_prompt("我要查下北京天气")
        │  │
        │  ├─ 构造messages
        │  │  messages = [
        │  │      SystemMessage(content=system_content),
        │  │      HumanMessage(content=user_content)
        │  │  ]
        │  │
        │  ├─ 调用LLM
        │  │  response = await self.llm.ainvoke(messages)
        │  │
        │  ├─ 提取命令
        │  │  command = response.content.strip()
        │  │
        │  │  # LLM输出示例:
        │  │  # ./user_skills/1001/clawhub_weather-cn/current/weather-cn.sh 北京
        │  │
        │  └─ 返回命令
        │
        ├─ 第3步: 修复命令中的路径
        │  fixed_command = fix_llm_command(raw_command, "clawhub_weather-cn", state)
        │  │ [agent/utils/fix_command.py]
        │  │
        │  ├─ Windows路径转换
        │  │  # 替换反斜杠为正斜杠
        │  │  command = command.replace("\\", "/")
        │  │
        │  ├─ 相对路径转绝对路径 (如需要)
        │  │  # 如果命令以 ./ 开头，替换为完整路径
        │  │
        │  └─ 特殊字符处理
        │     # 确保引号等特殊字符正确转义
        │
        ├─ 第4步: 执行Shell命令
        │  result = await _execute_external_skill_with_retry(
        │      "clawhub_weather-cn",
        │      skill_doc,
        │      fixed_command,
        │      "我要查下北京天气",
        │      retry_count=0,
        │      max_retries=3
        │  )
        │  │ [skill_query_node.py:213-274]
        │  │
        │  └─→ _execute_external_skill_with_retry()
        │      │
        │      ├─ 执行命令
        │      │  result = execute_agent_command(
        │      │      fixed_command,
        │      │      timeout_sec=300.0
        │      │  )
        │      │  │ [agent/utils/sub_agent_command.py]
        │      │  │
        │      │  └─→ execute_agent_command()
        │      │      │
        │      │      ├─ 使用asyncio subprocess执行
        │      │      │  proc = await asyncio.create_subprocess_shell(
        │      │      │      "./user_skills/1001/clawhub_weather-cn/current/weather-cn.sh 北京",
        │      │      │      stdout=asyncio.subprocess.PIPE,
        │      │      │      stderr=asyncio.subprocess.PIPE
        │      │      │  )
        │      │      │
        │      │      ├─ 等待结果
        │      │      │  stdout, stderr = await asyncio.wait_for(
        │      │      │      proc.communicate(),
        │      │      │      timeout=300.0
        │      │      │  )
        │      │      │
        │      │      ├─ 解码输出
        │      │      │  output = stdout.decode('utf-8', errors='replace')
        │      │      │
        │      │      │  # 脚本输出示例:
        │      │      │  # "北京: 晴, 15/2℃, 北风2级"
        │      │      │
        │      │      └─ 返回: "✅ 北京: 晴, 15/2℃, 北风2级"
        │      │            或 "❌ 命令执行失败: [错误信息]"
        │      │
        │      ├─ 检查是否成功
        │      │  if result.startswith("❌"):
        │      │      # 失败，进入重试
        │      │      if retry_count < max_retries:
        │      │          # 调用_generate_external_skill_command_with_error()
        │      │          # 将错误信息传给LLM，重新生成命令
        │      │          new_command = await _generate_external_skill_command_with_error(
        │      │              "clawhub_weather-cn",
        │      │              skill_doc,
        │      │              human_input,
        │      │              error_msg=result,  # "❌ 城市不存在"
        │      │              retry_count=1
        │      │          )
        │      │          # LLM可能会输出:
        │      │          # "./user_skills/1001/clawhub_weather-cn/current/weather-cn.sh 北京市"
        │      │
        │      │          fixed_command = fix_llm_command(new_command, "clawhub_weather-cn")
        │      │
        │      │          # 递归重试
        │      │          return await _execute_external_skill_with_retry(
        │      │              ...,
        │      │              command=fixed_command,
        │      │              retry_count=retry_count+1
        │      │          )
        │      │      else:
        │      │          # 达到最大重试次数
        │      │          return {"success": False, "content": result}
        │      │  else:
        │      │      # 成功
        │      │      return {"success": True, "content": result}
        │
        ├─ 第5步: 返回最终结果
        │  return {
        │      "skill_name": "clawhub_weather-cn",
        │      "success": result["success"],
        │      "content": result["content"],
        │      "command": fixed_command if result["success"] else None
        │  }
        │
        └─ (end of _execute_external_skill_async)
```

---

## 错误恢复流程

### 内部Skill的错误恢复

```
MySQLQuerySkill.call() 返回 SkillResult(success=False, content="...")
    │
    ▼
_execute_skill_with_retry() 中的重试逻辑 [skill_query_node.py:429-438]
    │
    ├─ 当前状态:
    │  - search_response.success = False
    │  - retry_count = 0
    │  - max_retries = 3
    │
    ├─ 第1次重试:
    │  │
    │  ├─ 添加错误恢复提示词到messages
    │  │  messages.append(
    │  │      SystemMessage(content=PROMPT_QUERY_ERROR_RETRY.format(
    │  │          error_content=search_response.content
    │  │      ))
    │  │  )
    │  │  │
    │  │  │ PROMPT_QUERY_ERROR_RETRY 内容:
    │  │  │ "前一次查询出现错误: {error_content}
    │  │  │
    │  │  │  可能的原因:
    │  │  │  1. 表名或字段名错误 -> 检查SKILL.md中的正确字段名
    │  │  │  2. WHERE条件错误 -> 调整查询条件
    │  │  │  3. 数据库连接问题 -> 重新尝试
    │  │  │
    │  │  │  请重新分析并尝试另一种方式查询。"
    │  │
    │  ├─ 重新调用LLM (注意: 之前的所有messages都保留，便于上下文学习)
    │  │  retry_response = await self.llm.ainvoke(messages)
    │  │  │
    │  │  │ messages现在包括:
    │  │  │ [
    │  │  │   SystemMessage: "Skill content:...\nNecessary data:...",
    │  │  │   HumanMessage: "我是谁",
    │  │  │   SystemMessage: "前一次查询出现错误:...",  # 新增
    │  │  │ ]
    │  │  │
    │  │  │ LLM可能输出修正的参数:
    │  │  │ {"medical_record_no": "1001", "table": "patients", ...}
    │  │
    │  ├─ 执行修正后的查询
    │  │  input_param = retry_response.content
    │  │  search_response = await handler.call_async(str(input_param))
    │  │
    │  ├─ 检查结果
    │  │  if search_response.success:
        │  │      # 成功，跳出循环
        │  │  else:
        │  │      # 继续重试
        │  │      retry_count = 1
        │  │
        │  └─ 返回结果
        │
    ├─ 第2-3次重试 (同样流程，retry_count += 1)
    │
    └─ 第4次尝试: 如果still failed且retry_count >= 3
        │
        └─ 跳出while循环，返回最后的failure结果
           {
               "skill_name": "mysql_query",
               "success": False,
               "content": "最后一次的错误信息"
           }
```

### 外部Skill的错误恢复

```
脚本执行返回: "❌ 城市不存在"
    │
    ▼
_execute_external_skill_with_retry() 中的重试逻辑 [skill_query_node.py:243-258]
    │
    ├─ 检查失败标记
    │  if result.startswith("❌"):
    │      # 进入错误恢复
    │
    ├─ 第1次重试:
    │  │
    │  ├─ 检查是否还有重试次数
    │  │  if retry_count < max_retries (0 < 3):
    │  │      # 可以重试
    │  │
    │  ├─ 调用_generate_external_skill_command_with_error()
    │  │  │ [skill_query_node.py:276-319]
    │  │  │
    │  │  ├─ 构造包含错误信息的messages
    │  │  │  system_content = get_external_skill_system_prompt(skill_doc)
    │  │  │  error_prompt = PROMPT_EXTERNAL_SKILL_ERROR_RETRY.format(
    │  │  │      error_content="❌ 城市不存在"
    │  │  │  )
    │  │  │
    │  │  │  messages = [
    │  │  │      SystemMessage(content=system_content),
    │  │  │      SystemMessage(content=error_prompt),  # 新增错误信息
    │  │  │      HumanMessage(content=human_input)
    │  │  │  ]
    │  │  │
    │  │  │ PROMPT_EXTERNAL_SKILL_ERROR_RETRY 内容:
        │  │  │ "前一次执行出现错误: {error_content}
        │  │  │
        │  │  │  请根据错误信息调整命令，可能的修改方案:
        │  │  │  1. 更换城市名称 (如果是城市名称错误)
        │  │  │  2. 修改命令参数格式
        │  │  │  3. 检查脚本是否存在
        │  │  │
        │  │  │  请重新输出修正的命令。"
    │  │  │
    │  │  ├─ 调用LLM得到修正命令
    │  │  │  response = await self.llm.ainvoke(messages)
    │  │  │  # LLM输出: "./user_skills/1001/clawhub_weather-cn/current/weather-cn.sh 北京市"
    │  │  │
    │  │  └─ 返回修正命令
    │  │
    │  ├─ 修复命令路径
    │  │  fixed_command = fix_llm_command(new_command, "clawhub_weather-cn")
    │  │
    │  ├─ 递归重新执行
    │  │  return await _execute_external_skill_with_retry(
    │  │      "clawhub_weather-cn",
    │  │      skill_doc,
    │  │      command=fixed_command,
    │  │      human_input,
    │  │      retry_count=1,  # 递增
    │  │      max_retries=3
    │  │  )
    │  │
    │  └─ 进入新的循环
    │
    ├─ 第2-3次重试 (同样流程，retry_count递增)
    │
    └─ 第4次尝试: 如果仍然失败且retry_count >= 3
        │
        └─ 跳出递归，返回最后的failure结果
           {
               "skill_name": "clawhub_weather-cn",
               "success": False,
               "content": "❌ 命令执行失败: ..."
           }
```

---

## 关键代码片段

### 1. Skill Registry 的初始化

```python
# skills/registry.py:32-37

SKILL_REGISTRY: Dict[str, SkillHandler] = {
    "mysql_query": MySQLQuerySkill(),
    "milvus_query": MilvusQuerySkill(),
    "web_search": WebSearchSkill(),
    "derma_image": ImageDetectHandler()
}

# 在应用启动时自动创建这些实例
# 所有实例都继承自SkillHandler基类，实现统一接口
```

### 2. LLM 结构化输出的调用

```python
# skills/base.py:67-79

async def execute_with_llm_async(self, llm, messages) -> SkillResult:
    """
    使用大型语言模型生成结构化参数并执行工具。
    """
    try:
        # 关键: with_structured_output() 强制LLM按schema格式输出
        structured_llm = llm.with_structured_output(self.schema)

        # LLM返回Pydantic对象，不是JSON字符串
        params_obj = await structured_llm.ainvoke(messages)

        if not params_obj:
            return SkillResult(False, "LLM 返回了空的结构化输出")

        # 转为JSON字符串后调用skill
        return await self.call_async(params_obj.model_dump_json())
    except Exception as e:
        return SkillResult(False, f"参数生成失败: {str(e)}")
```

### 3. Skill 并行执行

```python
# agent/nodes/skill_query_node.py:557-559

# 并行执行所有skill，使用asyncio.gather()
tasks = [
    self._execute_single_skill_async(name, state, human_input)
    for name in skill_names
]
results = await asyncio.gather(*tasks, return_exceptions=True)

# 返回结果列表，顺序与task列表相同
# 即使某个task失败，也会继续执行其他task
```

### 4. 结果处理和合并

```python
# agent/nodes/skill_query_node.py:490-512

def _process_skill_results(self, results: List, skill_names: List[str]) -> tuple[List[str], List[str]]:
    """
    处理所有技能执行结果

    Args:
        results: 技能执行结果列表 (可能包含异常对象)
        skill_names: 技能名称列表

    Returns:
        (successful_results, failed_results) 元组
    """
    successful_results = []
    failed_results = []

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # asyncio.gather(return_exceptions=True) 返回的异常
            logger.error(f"{skill_names[i]} 抛出异常: {result}")
            failed_results.append(f"{skill_names[i]}: 执行异常 - {str(result)}")
        elif result.get("success"):
            # 成功结果
            successful_results.append(
                f"**{result['skill_name']}** 查询结果:\n{result['content']}"
            )
        elif not result.get("silent"):
            # 失败但不是silent的结果
            failed_results.append(f"{result['skill_name']}: {result['content']}")

    return successful_results, failed_results
```

### 5. 最终结果合并

```python
# agent/nodes/skill_query_node.py:571-582

# 合并所有成功的结果
combined_result = "\n\n---\n\n".join(successful_results)

# 例如:
# "**mysql_query** 查询结果:
#  [{id:1001, name:张三}]
#
#  ---
#
#  **clawhub_weather-cn** 查询结果:
#  北京: 晴, 15/2℃"

return {
    "sub_agent_input": combined_result,
    "human_input": human_input  # 更新state中的human_input
}

# 这个结果会传给ChatNode，作为上下文信息
```

