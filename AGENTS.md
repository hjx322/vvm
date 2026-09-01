

<!-- Skills section removed -->

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: `npx openskills read <skill-name>` (run in your shell)
  - For multiple: `npx openskills read skill-one,skill-two`
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>derma_image</name>
<description>皮肤病图片检测技能，支持使用 YOLO 模型（YOLOv10/v11）进行皮肤病图片检测。</description>
<location>project</location>
</skill>

<skill>
<name>clawhub_weather-cn</name>
<description>中文天气查询工具 - 使用中国天气网获取实时天气（无需API密钥，不依赖大模型）。支持50+预置城市，<1秒响应，完全零Token消耗。当用户询问天气相关问题时使用。</description>
<location>project</location>
</skill>

<skill>
<name>milvus_query</name>
<description>【仅限医学实体】查询非患者特异性的公共医学知识（药物/疾病/胃肠文献）。支持两阶段检索与LLM重排，确保语义相关性。仅用于回答医学术语的定义、原理及通用治疗方案。注意：1. 严禁用于查询非医学名词（如娱乐/天天气/政治）；2. 不包含任何个人就诊记录。</description>
<location>project</location>
</skill>

<skill>
<name>mysql_query</name>
<description>: 专用于检索特定患者的结构化医疗档案（MySQL数据库）。当且仅当用户查询个人数据时使用，包括：身份信息（patient_info）、历史就诊与诊断记录（visit_record_list）、具体检查检验数值（examine_result_list）。注意：此工具不包含通用医学知识，仅返回客观记录。</description>
<location>project</location>
</skill>

<skill>
<name>web_search</name>
<description>通过 Tavily API 进行互联网搜索查询最新信息和通用知识。当用户询问实时信息、医学常识、药物信息或需要互联网上的最新数据时使用。</description>
<location>project</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
