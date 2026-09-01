# OpenSkills集成流程
**本方法在构筑的时候还没有成熟的方法让QWEN获得Skills能力，因此如果读到本文件时有更好的方法，应在比较后再做技术选型**
## 1. 基本信息
github: https://github.com/numman-ali/openskills    
本地项目: vvm-demo-digital-smart-doctor-agent  
文件创建日期：2025年12月24日  
最后一次更新日期：2026年1月26日
集成使用的操作系统：ubuntu 24.04 

## 2. 操作流程
### 2.1 **安装Node.js**
此次安装版本为  "node": "v24.12.0"  
官方建议 Node.js 20.6+

### 2.2 安装openskills 
详见github页面，这里不再给出详细的安装流程
```
https://github.com/numman-ali/openskills
```

### 2.3 添加自定义技能
从github的readme文件可以查看openskills的运作机理以及与claude的skills有什么不同，在这里不做赘述  
若上一步安装成功，在根目录下会生成一个文件夹:.claude 这个文件夹内维护了所有可用的Skill，可通过访问改文件夹下的skill文件夹直接查看所有技能的相关内容  
以添加向量数据库的操作为例：  
1. 创建技能路径
```
cd .claude/skills/
mkdir milvus_query
```
该文件夹下包含这个技能的所有内容   

2. 创建技能描述
```
cd milvus_query
touch SKILL.md
```
**注意：SKILL.md的文件名称不可更改，每个技能都必须包含这个全大写的SKILL.md文件**

3. （关键）修改技能描述  
具体添加格式见openskills以及skills的技能格式模板，或者直接把github链接发送给qwen让模型仿照他们的格式生成相关描述  

4. 更新Agent的技能列表
使用以下命令手动更新<available_skills>
```
openskills sync
```

5. 验证更新结果
```
openskills list
```

### 2.4 在Agent应用中添加Skill

1. 添加技能列表相关操作的提示词
```
system_prompt = f"""
You are an agent with access to the following skills:
{read_skills_xml_from_agents_md()}  # 读取 AGENTS.md 中的 <available_skills>

To use a skill, output exactly:
Bash("openskills read <skill_name>")

Do not make up skill names. Only use those listed above.
"""
```
其中，read_skills_xml_from_agents_md是使用CLI调用以下命令并返回字符串：
```
openskills list
```

2. 然后Agent就可以根据当前的对话主动判断是否发起技能列表的请求，当提问中包含了适用于skill的情况时，就会返回
```
openskills read <skill_name>
```
本次演示中通过检查字符串中是否出现了openskills read来判断是否进入技能调用的模式  

3. 然后使用一些正则化的方式手动提取出<skill_name>这里不建议直接使用大模型返回的bash命令，因为让远端大模型，尤其是会开放给公众的大模型直接使用本地命令行是一种非常危险的行为  
4. 使用相同的方法，把openskills read的内容返回给大模型，这里提供给大模型的主要是技能的详细使用内容，包括参数，格式等信息，这里可以扩充的内容非常多，鼓励各种行为的探索，但不建议让大模型直接操作  
5. 再次封装好的信息发送给大模型，然后回返回包含json格式的参数，同样使用正则化方法从字符串中提取出json格式的内容，完成了一次技能调用