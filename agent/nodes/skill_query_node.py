"""Skill 查询节点 包含同步和异步版本
选择合适技能，是否需要RAG
并行执行(最多3个)，带超时控制
合并结果（skill_query_node.py:401-423）：
把成功/失败结果区分开，汇总成 sub_agent_input，交给后面的 chat_node 生成最终回答
"""

import asyncio
import json
import os
from typing import List
import shutil
import subprocess
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from agent.core.state import DigitalSmartDoctorState
from agent.tools.skill_tools import make_skill_tools
from agent.utils.skill_executor import extract_skill_names
from prompt.query_result_prompt import (
    PROMPT_QUERY_ERROR_RETRY,
    PROMPT_QUERY_RESULT_RESPONSE,
)
from prompt.skills_prompt import get_skills_system_prompt
from prompt.external_skill_prompt import EXECUTE_SYSTEM_INSTRUCTION
from skills import SKILL_REGISTRY


class SkillQueryNode:
    """Skill 查询节点：调用外部 skills 获取信息"""

    def __init__(self, llm, skill_timeout: float = 300.0):
        """初始化 Skill 查询节点

        Args:
            llm: LLM 实例
            skill_timeout: 单个技能执行的超时时间（秒），默认 30 秒
        """
        self.llm = llm
        self.skill_timeout = skill_timeout
        # 缓存外部技能的SKILL.md文档，减少重复读取
        self._external_skill_docs_cache: dict = {}

    async def _run_npx_openskills_read_async(self, skill_name: str) -> str:
        """异步执行 npx openskills read 命令
        
        Args:
            skill_name: 技能名称
            
        Returns:
            技能内容字符串，如果失败则返回空字符串
        """
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            raise RuntimeError("npx not found in PATH")
        
        proc = await asyncio.create_subprocess_exec(
            npx, "-y", "openskills", "read", skill_name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            logger.error(f"openskills read {skill_name} failed: {stderr.decode('utf-8', errors='replace')}")
            return ""
        
        return stdout.decode('utf-8', errors='replace')

    async def _execute_external_skill_async(
        self,
        skill_name: str,
        human_input: str,
        state: DigitalSmartDoctorState,
    ) -> dict:
        """执行外部技能：统一 LLM 工具循环（load_skill_resource / execute_skill_script）。

        LLM 读取 SKILL.md 后自行决定调用哪个工具：
        - 读文档型（LLM-Native）：反复调用 load_skill_resource 加载 agents/、references/ 等文件
        - 执行脚本型（Shell）：调用 execute_skill_script 执行 .sh / .py 脚本

        Args:
            skill_name: 外部技能名称，如 clawhub_weather-cn 或 healthfit
            human_input: 用户输入
            state: 当前 Agent 状态

        Returns:
            包含 skill_name, success, content 的字典
        """
        try:
            # 1. 读取 SKILL.md（L2）
            skill_doc = await self._get_external_skill_documentation(skill_name, state)
            if not skill_doc:
                logger.error(f"无法读取外部技能文档: {skill_name}")
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": f"无法读取技能文档: {skill_name}",
                }

            # 2. 构造技能根目录，创建工具（绑定路径）
            base_dir = os.path.join(
                "./user_skills",
                state["medical_record_no"],
                skill_name,
                "current",
            )
            tools = make_skill_tools(os.path.realpath(base_dir))
            tools_by_name = {t.name: t for t in tools}

            # 3. 构造消息列表 + 绑定工具
            system_content = EXECUTE_SYSTEM_INSTRUCTION.format(
                skill_doc=skill_doc,
                human_input=human_input,
            )
            messages = [
                SystemMessage(content=system_content),
                HumanMessage(content=human_input),
            ]
            llm_with_tools = self.llm.bind_tools(tools)

            # 4. 工具循环（最多 10 轮，防止死循环）
            max_iterations = 10
            response = None
            for iteration in range(max_iterations):
                response = await llm_with_tools.ainvoke(messages)

                tool_calls = getattr(response, "tool_calls", None)
                if not tool_calls:
                    # LLM 不再调用工具，输出了最终回复
                    logger.info(
                        f"外部技能 {skill_name} 工具循环结束（{iteration + 1} 轮）"
                    )
                    break

                # 追加 AIMessage（含 tool_calls）
                messages.append(response)

                # 执行每个工具调用
                for tc in tool_calls:
                    tc_name = tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")
                    tc_args = tc["args"] if isinstance(tc, dict) else getattr(tc, "args", {})
                    tc_id   = tc["id"]   if isinstance(tc, dict) else getattr(tc, "id",   "")

                    tool_fn = tools_by_name.get(tc_name)
                    if tool_fn:
                        try:
                            tool_result = tool_fn.invoke(tc_args)
                            logger.debug(f"工具 {tc_name} 返回 {len(str(tool_result))} 字节")
                        except Exception as te:
                            tool_result = f"[工具 {tc_name} 执行失败: {te}]"
                            logger.warning(f"工具执行异常: {tc_name}: {te}")
                    else:
                        tool_result = f"[未知工具: {tc_name}]"

                    messages.append(
                        ToolMessage(content=str(tool_result), tool_call_id=tc_id)
                    )
            else:
                logger.warning(f"外部技能 {skill_name} 达到最大轮数 {max_iterations}，强制结束")

            final_content = (
                response.content if response and hasattr(response, "content") else ""
            ).strip()

            if not final_content:
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": "外部技能未返回有效内容",
                }

            return {
                "skill_name": skill_name,
                "success": True,
                "content": final_content,
            }

        except Exception as e:
            logger.exception(f"执行外部技能异常: {skill_name}")
            return {
                "skill_name": skill_name,
                "success": False,
                "content": f"执行外部技能异常: {str(e)}",
            }

    # ─────────────────────────────────────────────────────────────────────────
    # 以下方法已废弃，由上方统一工具循环替代，保留注释供参考
    # （_generate_external_skill_command / _execute_external_skill_with_retry /
    #   _generate_external_skill_command_with_error）
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_external_skill_documentation(
        self, skill_name: str, state: DigitalSmartDoctorState
    ) -> str:
        """读取外部技能的 SKILL.md 文档（带内存缓存）。

        Args:
            skill_name: 技能名称
            state: 当前 Agent 状态（用于获取 medical_record_no）

        Returns:
            SKILL.md 内容字符串；读取失败返回 None
        """
        # 检查缓存
        if skill_name in self._external_skill_docs_cache:
            logger.debug(f"使用缓存的 SKILL.md: {skill_name}")
            return self._external_skill_docs_cache[skill_name]

        try:
            skill_path = os.path.join(
                "./user_skills",
                state["medical_record_no"],
                skill_name,
                "current",
                "SKILL.md",
            )

            if not os.path.exists(skill_path):
                logger.error(f"SKILL.md 文件不存在: {skill_path}")
                return None

            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()
            self._external_skill_docs_cache[skill_name] = content
            logger.debug(f"成功读取 SKILL.md: {skill_name} ({len(content)} 字节)")
            return content

        except Exception as e:
            logger.error(f"读取 SKILL.md 失败 {skill_name}: {e}")
            return None



    async def _execute_single_skill_async(
        self, 
        skill_name: str, 
        state: DigitalSmartDoctorState, 
        human_input: str
    ) -> dict:
        """执行单个 skill 的完整流程（带超时控制）
        
        Args:
            skill_name: 技能名称
            state: 当前状态
            human_input: 用户输入
            
        Returns:
            包含 skill_name, success, content 的字典
        """
        

        try:
            # 使用 asyncio.wait_for 添加超时控制
            return await asyncio.wait_for(
                self._execute_skill_with_retry(skill_name, state, human_input),
                timeout=self.skill_timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"技能 {skill_name} 执行超时（{self.skill_timeout}秒）")
            return {
                "skill_name": skill_name,
                "success": False,
                "content": f"执行超时（{self.skill_timeout}秒）"
            }
    
    async def _execute_skill_with_retry(
        self,
        skill_name: str,
        state: DigitalSmartDoctorState,
        human_input: str
    ) -> dict:
        """执行单个 skill 的完整流程（内部方法，包含重试逻辑）

        Args:
            skill_name: 技能名称
            state: 当前状态
            human_input: 用户输入

        Returns:
            包含 skill_name, success, content 的字典
        """
        try:
            # 0. 检测是否为外部技能（clawhub_前缀）
            if skill_name not in SKILL_REGISTRY:
                logger.info(f"检测到外部技能: {skill_name}")
                return await self._execute_external_skill_async(skill_name, human_input,state)

            # 1. 获取 handler
            handler = SKILL_REGISTRY[skill_name]
            
            # 2. 并行执行：读取 skill 内容 + 准备必要数据
            skill_content, necessary_data = await asyncio.gather(
                self._run_npx_openskills_read_async(skill_name),
                handler.prepare_necessary_data_async(state),
                return_exceptions=True
            )
            
            # 检查异常
            if isinstance(skill_content, Exception):
                logger.error(f"获取技能详细内容失败{skill_name}: {skill_content}")
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": f"读取技能内容失败: {str(skill_content)}"
                }
            
            if isinstance(necessary_data, Exception):
                logger.error(f"数据准备失败{skill_name}: {necessary_data}")
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": f"准备必要数据失败: {str(necessary_data)}"
                }
            
            # 3. 检查必要数据
            if not necessary_data.success:
                logger.info(f"Skill {skill_name} missing necessary data: {necessary_data.content}")
                if necessary_data.content == "MySQL 查询缺少病历号":
                    return {
                        "skill_name": skill_name,
                        "success": False,
                        "content": "",
                        "silent": True  
                    }
                return {
                    "skill_name": skill_name,
                    "success": False,
                    "content": necessary_data.content
                }
            
            # 4. 使用 LLM 执行 skill
            messages = [
                SystemMessage(content=f"Skill content:\n{skill_content}, Necessary data:\n{necessary_data.content}"),
                HumanMessage(content=human_input)
            ]
            
            search_response = await handler.execute_with_llm_async(self.llm, messages)
            
            # 5. 重试机制（最多 3 次）
            retry_count = 0
            while not search_response.success and retry_count < 3:
                logger.warning(f"Skill {skill_name} execution failed, retrying ({retry_count + 1}/3): {search_response.content}")
                messages.append(
                    SystemMessage(content=PROMPT_QUERY_ERROR_RETRY.format(error_content=search_response.content))
                )
                retry_response = await self.llm.ainvoke(messages)
                input_param = retry_response.content
                search_response = await handler.call_async(str(input_param))
                retry_count += 1
            
            return {
                "skill_name": skill_name,
                "success": search_response.success,
                "content": search_response.content
            }
            
        except Exception as e:
            logger.exception(f"执行技能时发生异常：{skill_name}")
            return {
                "skill_name": skill_name,
                "success": False,
                "content": f"执行技能时发生异常: {str(e)}"
            }
    
    async def _extract_skill_names_from_llm(self, human_input: str,state: DigitalSmartDoctorState) -> List[str]:
        """从 LLM 响应中提取需要调用的技能名称
        
        Args:
            human_input: 用户输入
            
        Returns:
            技能名称列表，如果没有检测到技能调用则返回空列表
        """
        try:
            skills_system_prompt = get_skills_system_prompt(state)
        except Exception as e:
            logger.error(f"获取技能系统提示词失败: {e}")
            return []

        messages = [
            SystemMessage(content=skills_system_prompt),
            HumanMessage(content=human_input)
        ]

        # 使用 LLM 判断需要调用哪些 skills
        response = await self.llm.ainvoke(messages)
        response_content = response.content

        if "openskills read" not in response_content:
            logger.info("LLM响应中未检测到技能调用")
            return []

        try:
            skill_names = extract_skill_names(str(response_content))
            logger.info(f"调用技能: {skill_names}")
            return skill_names
        except ValueError as e:
            logger.error(f"解析技能名称失败: {e}")
            return []
    
    def _process_skill_results(self, results: List, skill_names: List[str]) -> tuple[List[str], List[str]]:
        """处理所有技能执行结果
        
        Args:
            results: 技能执行结果列表
            skill_names: 技能名称列表
            
        Returns:
            (successful_results, failed_results) 元组
        """
        successful_results = []
        failed_results = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"{skill_names[i]} 抛出异常: {result}")
                failed_results.append(f"{skill_names[i]}: 执行异常 - {str(result)}")
            elif result.get("success"):
                successful_results.append(f"**{result['skill_name']}** 查询结果:\n{result['content']}")
            elif not result.get("silent"):  
                failed_results.append(f"{result['skill_name']}: {result['content']}")
        
        return successful_results, failed_results
    
    async def _generate_final_response(self, combined_result: str, human_input: str) -> str:
        """使用 LLM 基于所有查询结果生成最终回答
        
        Args:
            combined_result: 合并后的查询结果
            human_input: 用户输入
            
        Returns:
            LLM 生成的最终回答
        """
        final_messages = [
            SystemMessage(content=PROMPT_QUERY_RESULT_RESPONSE.format(search_result=combined_result)),
            HumanMessage(content=human_input)
        ]
        llm_response = await self.llm.ainvoke(final_messages)
        return str(llm_response.content)

    async def execute_async(self, state: DigitalSmartDoctorState) -> dict:
        """异步版本的 skill 查询节点，支持并行执行多个 skills
        
        Args:
            state: 当前状态
            
        Returns:
            包含 sub_agent_input 的状态字典
        """
        human_input = str(state["human_input"])
        def get_skill_name(state):
            try:
                data = json.loads(state.get('sub_agent_input', '{}'))
                return data.get('skill_name')
            except (json.JSONDecodeError, TypeError, AttributeError):
                return None

        img_skill = get_skill_name(state)
        if img_skill:
            human_input = json.loads(state.get('sub_agent_input', '{}')).get('image_path') + "帮我识别一下这张图片"
        
        # 1. 从 LLM 响应中提取需要调用的技能
        skill_names =[img_skill] if img_skill else await self._extract_skill_names_from_llm(human_input,state)
        if not skill_names:
            return {}
        
        # 2. 并行执行所有 skills
        tasks = [self._execute_single_skill_async(name, state, human_input) for name in skill_names]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 3. 处理结果
        successful_results, failed_results = self._process_skill_results(results, skill_names)
        
        # 4. 如果没有任何成功结果，返回空
        if not successful_results:
            if failed_results:
                logger.warning(f"所有技能执行失败: {failed_results}")
            return {}
        
        # 5. 合并所有成功的结果
        combined_result = "\n\n---\n\n".join(successful_results)
        
        # 如果有失败的 skill，也记录到日志
        if failed_results:
            logger.warning(f"部分技能执行失败: {failed_results}")
        
        # 6. 生成最终回答
        #final_response = await self._generate_final_response(combined_result, human_input)

        return {
            "sub_agent_input": combined_result,
            "human_input": human_input  # 更新 state 中的 human_input
        }

