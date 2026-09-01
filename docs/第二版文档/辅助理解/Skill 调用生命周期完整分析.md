# Skill 调用生命周期完整分析

## 目录
1. [概述](#概述)
2. [Skill 读取机制](#skill-读取机制)
3. [完整调用生命周期](#完整调用生命周期)
4. [内部 Skill 执行流程](#内部-skill-执行流程)
5. [外部 Skill 执行流程](#外部-skill-执行流程)
6. [关键数据结构](#关键数据结构)
7. [代码追踪](#代码追踪)

---

## 概述

VVM项目中的Skill系统是一个两层架构，支持**内部Skill**和**外部Skill**的调用：

| 类型 | 例子 | 加载方式 | 执行方式 |
|------|------|--------|--------|
| **内部Skill** | `mysql_query`, `milvus_query`, `web_search`, `derma_image` | 代码注册（SKILL_REGISTRY） | Python异步方法直接调用 |
| **外部Skill** | `clawhub_weather-cn`, `clawhub_*` | OpenSkills框架 + npx命令 | Shell脚本执行 |

---

## Skill 读取机制

### 1. 内部Skill 的读取

**位置**: `skills/registry.py`

```python
SKILL_REGISTRY: Dict[str, SkillHandler] = {
    "mysql_query": MySQLQuerySkill(),
    "milvus_query": MilvusQuerySkill(),
    "web_search": WebSearchSkill(),
    "derma_image": ImageDetectHandler()
}
```

**特点**：
- 在应用启动时直接实例化
- 所有Skill继承自`SkillHandler`基类（`skills/base.py`）
- 提供统一的接口：
  - `prepare_necessary_data_async()`: 准备执行所需的必要数据
  - `call_async()`: 执行Skill逻辑
  - `execute_with_llm_async()`: 使用LLM生成参数后执行

### 2. 外部Skill 的读取



```bash

```

**位置**: `agent/nodes/skill_query_node.py` 中的 `_run_npx_openskills_read_async()`

**工作流程**：
```
用户输入 "北京天气"
    ↓
检测到skill: clawhub_weather-cn
    ↓
读取skill目录: ./user_skills/<patient_id>/clawhub_weather-cn/current/SKILL.md
    ↓
提取SKILL.md内容 → 传给LLM
    ↓
LLM根据SKILL.md生成执行命令
    ↓
执行命令获取结果
```

---

## 完整调用生命周期

### 整体流程图

```
┌─────────────────────────────────────────────────────────┐
│  1. 用户输入 (human_input)                              │
│     "我是张三，查下我的药物和北京天气"                  │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│  2. PreProcessNode: 预处理输入                          │
│     - 检测是否有图片上传                                │
│     - 准备state初始化                                  │
└────────────────────┬────────────────────────────────────┘
                     │
            ┌────────┴────────┐
            │                 │
    有图片 yes             有图片 no
            │                 │
            ▼                 ▼
    ┌──────────────┐  ┌──────────────────────┐
    │ImageProcess  │  │RoutingNode          │
    │Node          │  │ 条件分支检查       │
    └──────────────┘  └──────┬───────────────┘
            │                 │
            └────────┬────────┘
                     │
    ┌────────────────┴──────────────────────────────────────┐
    │  3. SkillQueryNode: 技能查询和执行                   │
    │     （这是整个lifecycle最核心的部分）                │
    └────────────────┬──────────────────────────────────────┘
                     │
    ┌────────────────┴──────────────────────────────────────┐
    │  3.1 LLM意图识别: 需要调用哪些skills?               │
    │                                                        │
    │  输入: get_skills_system_prompt(state)               │
    │      + 用户原始输入                                  │
    │  输出: "openskills read mysql_query\n                │
    │         openskills read clawhub_weather-cn"          │
    └────────────────┬──────────────────────────────────────┘
                     │
    ┌────────────────┴──────────────────────────────────────┐
    │  3.2 提取skill名称                                   │
    │                                                        │
    │  skill_names = ["mysql_query",                       │
    │                 "clawhub_weather-cn"]                │
    └────────────────┬──────────────────────────────────────┘
                     │
    ┌────────────────┴──────────────────────────────────────┐
    │  3.3 并行执行多个skills (asyncio.gather)            │
    │                                                        │
    │  ├─ Task 1: 执行 mysql_query                        │
    │  │  ├─ 获取handler: SKILL_REGISTRY["mysql_query"]   │
    │  │  ├─ 准备必要数据: prepare_necessary_data_async() │
    │  │  ├─ npx read获取skill.md内容                     │
    │  │  ├─ 使用LLM生成参数: execute_with_llm_async()   │
    │  │  └─ 执行: call_async(params)                     │
    │  │     └─ 返回: {skill_name, success, content}      │
    │  │                                                     │
    │  └─ Task 2: 执行 clawhub_weather-cn                │
    │     ├─ 读取./user_skills/*/clawhub_weather-cn/SKILL.md
    │     ├─ 使用LLM根据SKILL.md生成shell命令            │
    │     ├─ 执行shell命令 (asyncio subprocess)           │
    │     └─ 返回: {skill_name, success, content}         │
    │                                                        │
    │  等待所有task完成                                    │
    └────────────────┬──────────────────────────────────────┘
                     │
    ┌────────────────┴──────────────────────────────────────┐
    │  3.4 处理所有结果                                     │
    │                                                        │
    │  成功结果:                                             │
    │  - "**mysql_query** 查询结果: {...}"                 │
    │  - "**clawhub_weather-cn** 查询结果: 北京：15/2°C"   │
    │                                                        │
    │  失败结果:                                             │
    │  - "未知技能: xxx"                                    │
    │  - "执行超时"                                         │
    │                                                        │
    │  合并: combined_result = "结果1\n\n---\n\n结果2"     │
    └────────────────┬──────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│  4. ChatNode: 生成最终回答                              │
│     - 输入: combined_result + 原始human_input            │
│     - 使用PROMPT_QUERY_RESULT_RESPONSE模板              │
│     - 由LLM根据skill结果生成自然语言回答                │
└────────────────┬────────────────────────────────────────┘
                 │
┌─────────────────┴────────────────────────────────────────┐
│  5. PostProcessNode: 后处理和输出格式化                 │
│     - 格式化最终回答                                     │
│     - 添加元数据（时间戳等）                            │
│     - 保存到state                                        │
└─────────────────┬────────────────────────────────────────┘
                  │
┌──────────────────┴───────────────────────────────────────┐
│  6. 返回给用户                                           │
│     final_answer: "根据你的药物信息... 北京今天15/2°C"  │
└──────────────────────────────────────────────────────────┘
```

---

## 内部 Skill 执行流程

### 代码位置
- **节点**: `agent/nodes/skill_query_node.py` 中的 `_execute_skill_with_retry()` (第355-452行)
- **基类**: `skills/base.py`
- **具体实现**: `skills/mysql_query_skill.py` 等

### 详细步骤

#### 第1步: 并行获取Skill信息和必要数据
```python
# 代码位置: skill_query_node.py:381-385
skill_content, necessary_data = await asyncio.gather(
    self._run_npx_openskills_read_async(skill_name),  # 读取SKILL.md（如果存在）
    handler.prepare_necessary_data_async(state),       # 准备必要数据
    return_exceptions=True
)
```

**prepare_necessary_data_async() 的作用**：
- MySQL Skill: 检查state中是否有`medical_record_no`和`crm`
- Milvus Skill: 检查查询参数是否合理
- Web Search Skill: 检查搜索关键词
- Derma Image Skill: 检查图片路径

示例 (MySQL Skill):
```python
def prepare_necessary_data(self, state) -> NecessaryDataResult:
    if "medical_record_no" not in state:
        return NecessaryDataResult(False, "MySQL 查询缺少病历号")
    if "crm" not in state:
        return NecessaryDataResult(False, "MySQL 查询缺少CRM参数")

    return NecessaryDataResult(
        True,
        f"病历号/medical_record_no:{state['medical_record_no']}\
         crm:{state['crm']}"
    )
```

**如果返回失败**，直接返回错误，不继续执行。

#### 第2步: 使用LLM生成结构化参数

```python
# 代码位置: skill_query_node.py:421-426
messages = [
    SystemMessage(content=f"Skill content:\n{skill_content}, Necessary data:\n{necessary_data.content}"),
    HumanMessage(content=human_input)
]

search_response = await handler.execute_with_llm_async(self.llm, messages)
```

**execute_with_llm_async() 做什么**：
1. 调用 `llm.with_structured_output(self.schema)` 获取结构化LLM
2. LLM根据Skill schema 生成结构化参数（Pydantic对象）
3. 调用 `call_async(params.model_dump_json())` 执行

示例 (MySQL Skill):
```python
# base.py:67-79
async def execute_with_llm_async(self, llm, messages) -> SkillResult:
    try:
        structured_llm = llm.with_structured_output(self.schema)
        params_obj = await structured_llm.ainvoke(messages)  # LLM生成参数
        if not params_obj:
            return SkillResult(False, "LLM 返回了空的结构化输出")
        return await self.call_async(params_obj.model_dump_json())  # 执行skill
    except Exception as e:
        return SkillResult(False, f"参数生成失败: {str(e)}")
```

#### 第3步: 执行Skill逻辑

以MySQL Skill为例：
```python
# mysql_query_skill.py:51-68
def call(self, input_param: str) -> SkillResult:
    try:
        # 从JSON字符串中提取参数
        start = input_param.find("{")
        if start == -1:
            start = 0

        # 调用底层search函数（带重试机制）
        content = search_with_retry(str(input_param[start:]), max_retries=3)

        return SkillResult(success=True, content=content)
    except Exception as e:
        return SkillResult(success=False, error=f"MySQL 查询失败: {str(e)}")
```

**search_with_retry() 的作用**：
- 执行SQL查询
- 如果连接断开，自动重试（最多3次）
- 返回JSON格式的查询结果

#### 第4步: 重试机制（失败时）

```python
# skill_query_node.py:428-438
retry_count = 0
while not search_response.success and retry_count < 3:
    logger.warning(f"Skill {skill_name} execution failed, retrying ({retry_count + 1}/3)")

    # 添加错误恢复提示词
    messages.append(
        SystemMessage(content=PROMPT_QUERY_ERROR_RETRY.format(error_content=search_response.content))
    )

    # 重新调用LLM和Skill
    retry_response = await self.llm.ainvoke(messages)
    input_param = retry_response.content
    search_response = await handler.call_async(str(input_param))
    retry_count += 1
```

**重试流程**：
1. 检测失败原因
2. 添加"错误恢复"提示词到messages
3. 让LLM重新分析并生成修正的参数
4. 重新执行Skill
5. 最多重试3次

---

## 外部 Skill 执行流程

### 代码位置
- **节点**: `agent/nodes/skill_query_node.py` 中的 `_execute_external_skill_async()` (第72-128行)

### 详细步骤

#### 第1步: 读取SKILL.md文档

```python
# skill_query_node.py:147-158
skill_path = os.path.join("./user_skills", state['medical_record_no'], skill_name, "current", "SKILL.md")

if not os.path.exists(skill_path):
    logger.error(f"SKILL.md文件不存在: {skill_path}")
    return None

with open(skill_path, 'r', encoding='utf-8') as f:
    content = f.read()
    # 缓存内容，减少重复读取
    self._external_skill_docs_cache[skill_name] = content
    return content
```

**目录结构**：
```
./user_skills
  └── <patient_id>           # 患者ID
      └── clawhub_weather-cn # 技能名称
          ├── current         # 当前版本
          │   ├── SKILL.md    # 技能文档（包含usage说明）
          │   ├── weather-cn.sh   # 执行脚本
          │   └── weather_codes.txt # 支持的城市编码
          └── v1
              └── ...         # 历史版本
```

#### 第2步: 使用LLM生成执行命令

```python
# skill_query_node.py:164-211
system_content = get_external_skill_system_prompt(skill_name, skill_doc)
user_content = get_external_skill_user_prompt(human_input)

messages = [
    SystemMessage(content=system_content),
    HumanMessage(content=user_content)
]

response = await self.llm.ainvoke(messages)
command = response.content.strip()
```

**系统提示词模板** (external_skill_prompt.py):
```
你是一个技能调度器。
根据以下SKILL.md文档和用户请求，生成要执行的命令。

SKILL.md内容:
{skill_doc}

用户请求: {human_input}

输出: 直接输出要执行的命令，不要有其他解释。
```

**LLM输出示例**：
```bash
openskills execute clawhub_weather-cn ./weather-cn.sh 北京
```

#### 第3步: 修复命令中的路径

```python
# skill_query_node.py:106
fixed_command = fix_llm_command(raw_command, skill_name, state)
```

**修复的内容**：
- Windows路径转换为Unix路径（`\` → `/`）
- 相对路径替换为绝对路径
- 特殊字符转义

#### 第4步: 执行Shell命令

```python
# skill_query_node.py:236-237
result = execute_agent_command(command, timeout_sec=self.skill_timeout)
```

**执行方式**：
```python
# agent/utils/sub_agent_command.py
async def execute_agent_command(command: str, timeout_sec: float):
    # 使用subprocess执行命令
    # 捕获stdout和stderr
    # 返回结果
```

**示例执行**：
```bash
# 命令
./user_skills/张三/clawhub_weather-cn/current/weather-cn.sh 北京

# 输出
天气：晴
温度: 15/2℃
风力: 2级
```

#### 第5步: 错误检查和重试

```python
# skill_query_node.py:240-264
if result.startswith("❌"):
    logger.warning(f"命令执行失败: {result}")

    # 如果失败且还有重试次数，尝试重新生成命令
    if retry_count < max_retries:
        logger.info(f"重试生成命令 ({retry_count + 1}/{max_retries})")

        # 调用_generate_external_skill_command_with_error()
        # 将错误信息传给LLM
        new_command = await self._generate_external_skill_command_with_error(...)

        # 修复并重新执行
        fixed_command = fix_llm_command(new_command, skill_name)
        return await self._execute_external_skill_with_retry(...)
```

**重试流程**：
1. 检测命令是否以"❌"开头
2. 提取错误信息
3. 将错误传给LLM，要求重新生成命令
4. 执行新命令
5. 最多重试3次

---

## 关键数据结构

### 1. SkillResult (base.py)

```python
@dataclass
class SkillResult:
    success: bool        # 执行是否成功
    content: str         # 结果内容
    raw: Any = None      # 原始数据（可选）
```

### 2. NecessaryDataResult (base.py)

```python
@dataclass
class NecessaryDataResult:
    success: bool   # 数据是否完整
    content: str    # 数据内容或错误信息
```

### 3. DigitalSmartDoctorState

关键字段（用于Skill执行）：
```python
class DigitalSmartDoctorState(TypedDict):
    # 用户和会话信息
    human_input: str              # 用户输入
    medical_record_no: str        # 患者ID（MySQL需要）
    crm: str                      # CRM数据库名称
    doctor_id: str                # 医生ID（用于查询启用的skill）

    # Skill执行结果
    sub_agent_input: str          # Skill查询结果（合并后）

    # 对话历史
    messages: List[BaseMessage]   # 完整对话记录

    # 图片处理相关
    image_path: str               # 上传的图片路径
    image_processing_status: str  # 处理状态

    # LLM生成的最终回答
    final_answer: str
```

### 4. Skill Schema (schemas.py)

**MySQLQuerySchema** 示例：
```python
class MySQLQuerySchema(BaseModel):
    medical_record_no: str = Field(..., description="患者病历号")
    crm: str = Field(..., description="CRM数据库名称")
    db_name: str = Field(default="medical", description="查询数据库")
    table: str = Field(..., description="查询表名")
    where_clause: str = Field(..., description="WHERE子句")
```

LLM需要根据SKILL.md的参数定义生成上述结构。

---

## 代码追踪

### 1. LLM 意图识别

**流程**：
```
SkillQueryNode.execute_async()
    ↓
_extract_skill_names_from_llm()
    ↓
get_skills_system_prompt(state)  [在prompt/skills_prompt.py中定义]
    ↓
LLM分析用户输入，输出"openskills read xxx"
    ↓
extract_skill_names()  [提取openskills命令中的skill名称]
    ↓
返回: ["mysql_query", "clawhub_weather-cn"]
```

**关键提示词**：
```python
# prompt/skills_prompt.py:67-132

SYSTEM_PROMPT = """你是一个**智能任务调度器**。
你的目标是分析用户输入，并输出需要执行的**所有**工具指令。

### 工具清单
{skills}  # 从get_skills_description()动态生成

### 调度流程 (必须严格执行)

**第一步：意图扫描与工具匹配**
扫描用户输入，匹配**所有**相关的工具（可多选）：

#### 医疗领域工具 (严格限制为医疗相关)
1. 涉及"我"、"病历"、"检查结果" -> 选中 `mysql_query`
2. 涉及"疾病定义"、"药物说明"、"医学知识" -> 选中 `milvus_query`
3. 涉及其他医疗意图 -> 选中对应医疗工具

#### 外部工具 (无领域限制)
- **天气相关**（"天气"、"温度"、"下雨"）→ 调用 `clawhub_weather-cn`
- **其他外部技能**（`clawhub_`前缀）→ 按用户需求调用

### 智能决策
- ✅ 用户问"北京天气" -> 直接调用 `clawhub_weather-cn`
- ✅ 用户问"我是谁" -> 调用 `mysql_query`
- ❌ 用户问"特朗普是谁" -> 拒绝

### 输出格式
仅输出指令或回复，不要有分析过程。
- 并行调用示例：
  openskills read mysql_query
  openskills read milvus_query
- 拒绝示例：
  REPLY: 我无法处理这个请求。
"""
```

### 2. Skill 执行

**流程**：
```
_extract_skill_names_from_llm()
    ↓
返回 skill_names = ["mysql_query", ...]
    ↓
asyncio.gather(
    _execute_single_skill_async("mysql_query", state, human_input),
    _execute_single_skill_async("...", state, human_input),
    ...
)  # 并行执行
    ↓
对每个skill，执行 _execute_skill_with_retry()
    │
    ├─ 内部skill (mysql_query等)
    │  ├─ 检查SKILL_REGISTRY
    │  ├─ 获取handler
    │  ├─ handler.prepare_necessary_data_async(state)
    │  ├─ 并行: npx openskills read + 数据准备
    │  ├─ handler.execute_with_llm_async(llm, messages)
    │  │  ├─ LLM根据schema生成参数
    │  │  └─ handler.call_async(params)
    │  ├─ 失败时重试（最多3次）
    │  └─ 返回SkillResult
    │
    └─ 外部skill (clawhub_*)
       ├─ 读取SKILL.md
       ├─ LLM生成执行命令
       ├─ 修复路径
       ├─ 执行shell命令
       ├─ 失败时重新生成命令并重试
       └─ 返回结果
    ↓
_process_skill_results()
    ↓
合并成功结果，记录失败结果
    ↓
返回: {"sub_agent_input": combined_result}
```

### 3. LLM 最终回答生成

**流程**：
```
ChatNode.execute()
    ↓
构造messages:
  - SystemMessage: 医生角色提示词 (chat_prompt.py)
  - 对话历史 (state['messages'])
  - 如果有skill结果，添加查询结果上下文

    ↓
LLM生成最终回答
    ↓
返回: {
    "messages": [...],  # 添加新的AIMessage
    "final_answer": "根据你的信息..."
}
```

---

## 完整执行示例

### 用户输入
```
"我叫张三，病历号1001，查下我上次吃的什么药，顺便告诉我北京天气"
```

### 第1步: 意图识别
LLM分析，输出：
```
openskills read mysql_query
openskills read clawhub_weather-cn
```

### 第2步: 并行执行两个Skill

**Task 1: mysql_query**
```
1. 获取handler: MySQLQuerySkill()
2. 准备必要数据:
   - medical_record_no = "1001"
   - crm = "hn_db"
3. 读取SKILL.md (如果有)
4. LLM生成参数:
   {
     "medical_record_no": "1001",
     "crm": "hn_db",
     "table": "patient_medications",
     "where_clause": "medical_record_no = '1001' ORDER BY date DESC LIMIT 5"
   }
5. 执行: search_with_retry(...)
6. 返回:
   {
     "skill_name": "mysql_query",
     "success": true,
     "content": "[{药物: 阿莫西林, 用法: 每日3次, ...}, ...]"
   }
```

**Task 2: clawhub_weather-cn**
```
1. 读取SKILL.md: ./user_skills/1001/clawhub_weather-cn/current/SKILL.md
2. LLM根据SKILL.md生成命令:
   "./user_skills/1001/clawhub_weather-cn/current/weather-cn.sh 北京"
3. 修复路径(如果需要)
4. 执行命令
5. 返回:
   {
     "skill_name": "clawhub_weather-cn",
     "success": true,
     "content": "北京: 晴, 15/2℃, 北风2级"
   }
```

### 第3步: 合并结果
```
combined_result = """
**mysql_query** 查询结果:
[{药物: 阿莫西林, 用法: 每日3次, ...}, ...]

---

**clawhub_weather-cn** 查询结果:
北京: 晴, 15/2℃, 北风2级
"""
```

### 第4步: ChatNode 生成最终回答
LLM的输入：
```
System: 你是一位资深皮肤科医生...
        查询结果:
        [mysql和weather的结果]

        根据上述信息回答患者问题。

User: "我叫张三，病历号1001，查下我上次吃的什么药，顺便告诉我北京天气"
```

LLM的输出：
```
根据您的病历记录，您上次开的药物是阿莫西林，
用法是每日3次，饭后服用。

关于北京天气，今天晴天，气温15到2℃，北风2级。
建议您出门时注意保暖。
```

### 第5步: PostProcessNode 格式化和返回

---

## 关键设计模式

### 1. 并行执行多个Skill
使用 `asyncio.gather()` 同时执行多个Skill，提高响应速度。

### 2. 两层参数生成
- **第一层**：LLM决定调用哪些Skill
- **第二层**：对每个Skill，LLM生成具体参数

### 3. 自动重试机制
- 内部Skill失败时，重新调整参数重试
- 外部Skill失败时，重新生成命令重试
- 最多重试3次

### 4. 分离问题关注点
- SkillHandler: 只关心如何执行
- SkillQueryNode: 只关心何时执行、参数如何生成
- ChatNode: 只关心如何将Skill结果转化为自然语言回答

### 5. 动态Skill注册
- 内部Skill在启动时注册
- 外部Skill可以动态从文件读取
- 支持多医生、多用户的自定义Skill

---

## 常见问题排查

### Q1: 为什么某个Skill没有被调用？
**检查清单**：
1. 检查 `skills_prompt.py` 中的Skill清单是否包含该Skill
2. 检查 `get_skills_description()` 是否返回正确的启用Skill列表
3. 检查LLM是否正确识别用户意图

### Q2: Skill执行超时怎么办？
**检查清单**：
1. 检查 `skill_timeout` 设置（默认300秒）
2. 检查底层数据库/API是否响应缓慢
3. 考虑增加超时时间或优化查询

### Q3: 外部Skill读取SKILL.md失败？
**检查清单**：
1. 检查路径是否正确: `./user_skills/{patient_id}/{skill_name}/current/SKILL.md`
2. 检查SKILL.md文件是否存在
3. 检查文件编码是否为UTF-8

### Q4: LLM生成的参数不对？
**检查清单**：
1. 检查Skill的schema定义是否清晰
2. 检查SKILL.md中的说明是否详细
3. 考虑调整LLM的temperature参数
4. 可以添加schema validation来检测参数错误

---

## 性能优化建议

1. **缓存Skill文档**：已实现（`_external_skill_docs_cache`）
2. **连接池**：对MySQL使用连接池（`pool_recycle=3600`）
3. **并行执行**：使用`asyncio.gather()`并行执行多个Skill
4. **超时控制**：设置合理的超时时间，避免长时间等待
5. **错误快速失败**：如果必要数据缺失，立即返回，不继续执行

