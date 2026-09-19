from langchain_core.prompts import PromptTemplate

# 查询错误重试 Prompt
PROMPT_QUERY_ERROR_RETRY = """查询错误:
{error_content}

请检查参数是否正确，并且重新发起查询。"""

# 查询结果回答 Prompt
PROMPT_QUERY_RESULT_RESPONSE = """Search result:
{search_result}

请根据检索出的信息回答用户提问。"""
