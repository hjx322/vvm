import sys
import json
from typing import Dict, Any, List, TypedDict
from pathlib import Path
from datetime import datetime
from loguru import logger

# 添加当前目录到路径
search_scripts_path = str(Path(__file__).parent)
if search_scripts_path not in sys.path:
    sys.path.insert(0, search_scripts_path)

# 添加项目根目录到路径，以便导入 config 模块
project_root = str(Path(__file__).parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None


class SearchResult(TypedDict):
    """
    Structure for search results.
    """
    query: str  # Search query
    source: str  # Data source (tavily)
    results: List[dict]  # List of individual results
    timestamp: str  # Timestamp of search
    total_results: int  # Total number of results returned


def load_input(raw: str) -> Dict[str, Any]:
    """解析 --input JSON 字符串"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON input: {e}")


def eprint(*args):
    """输出到 stderr"""
    print(*args, file=sys.stderr)


def search(input_str: str, api_key: str = None) -> str:
    """
    使用 Tavily API 执行网络搜索

    Args:
        input_str: JSON格式的输入，包含 query 和 max_results
        api_key: Tavily API key，如果为 None 则从环境变量读取

    Returns:
        str: 格式化的搜索结果
    """
    try:
        data = load_input(input_str)
        query = str(data.get("query", "")).strip()
        max_results = int(data.get("max_results", 5))
        search_depth = str(data.get("search_depth", "advanced")).lower()

        if not query:
            return "错误: 搜索关键词(query)不能为空"

        # 限制搜索结果数量
        max_results = min(max(max_results, 1), 20)

        # 验证搜索深度
        if search_depth not in ["basic", "advanced"]:
            search_depth = "basic"

        # 获取 API key
        if not api_key:
            api_key = get_tavily_api_key()

        if not api_key:
            return "错误: 未找到 Tavily API key，请检查配置"

        logger.info(f"Executing Tavily search: query={query}, max_results={max_results}, depth={search_depth}")

        # 初始化 Tavily 客户端
        if TavilyClient is None:
            return "错误: tavily-python 库未安装"

        try:
            client = TavilyClient(api_key=api_key)

            # 执行搜索
            logger.info(f"开始搜索，关键词: {query}")
            response = client.search(
                query=query,
                max_results=max_results,
                search_depth=search_depth,
                include_domains=data.get("include_domains"),
                exclude_domains=data.get("exclude_domains")
            )

            logger.info(f"搜索完成，共获得 {len(response.get('results', []))} 个结果")

            # 格式化搜索结果
            results = []
            for i, item in enumerate(response.get('results', []), 1):
                title = item.get('title', '')
                url = item.get('url', '')
                snippet = item.get('content', '')
                score = item.get('score', 0.0)

                # 使用 loguru 记录每个搜索结果
                snippet_preview = snippet[:80] + "..." if len(snippet) > 80 else snippet
                logger.info(f"  结果[{i}] | 标题: {title} | 相关性: {score:.2f}")
                logger.info(f"           | 链接: {url}")
                if snippet_preview:
                    logger.info(f"           | 摘要: {snippet_preview}")

                result_item = {
                    'rank': i,
                    'title': title,
                    'url': url,
                    'snippet': snippet,
                    'relevance_score': score,
                    'published_date': item.get('published_date'),
                }
                results.append(result_item)

            # 构建返回结果
            search_result: SearchResult = {
                'query': query,
                'source': 'tavily',
                'results': results,
                'timestamp': datetime.now().isoformat(),
                'total_results': len(results)
            }

            logger.info(f"搜索结果已格式化，准备返回")
            return format_search_results(search_result)

        except Exception as e:
            error_msg = f"Tavily 搜索失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return error_msg

    except Exception as e:
        eprint(f"[search.py ERROR] {e}")
        logger.error(f"Web search error: {e}", exc_info=True)
        return f"搜索过程中出错: {str(e)}"


def get_tavily_api_key() -> str:
    """从配置或环境变量获取 Tavily API key"""
    import os

    # 首先尝试从环境变量获取
    api_key = os.getenv("TAVILY_API_KEY")
    if api_key:
        return api_key

    # 尝试从配置文件读取
    try:
        # 方案 1: 直接导入 (当从 registry 调用时有效)
        try:
            from config.app_config import load_config
            config = load_config()
            if hasattr(config, 'search') and hasattr(config.search, 'tavily'):
                return config.search.tavily.api_key
        except ImportError:
            # 方案 2: 查找项目根目录并添加到 sys.path
            project_root = _find_project_root()
            if project_root and str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            from config.app_config import load_config
            config = load_config()
            if hasattr(config, 'search') and hasattr(config.search, 'tavily'):
                return config.search.tavily.api_key
    except Exception as e:
        logger.debug(f"Failed to load config: {e}")

    return None


def _find_project_root() -> Path:
    """查找项目根目录（包含 pyproject.toml 或 application.yaml 的目录）"""
    current = Path(__file__).parent

    # 最多向上查找 10 级目录
    for _ in range(10):
        if (current / "pyproject.toml").exists() or (current / "application.yaml").exists():
            return current
        if (current / ".git").exists():  # 如果找到 .git 目录也认为是项目根
            return current
        parent = current.parent
        if parent == current:  # 到达文件系统根目录
            break
        current = parent

    # 如果没找到，返回预期的根目录
    return Path(__file__).parent.parent.parent.parent


def format_search_results(result: SearchResult) -> str:
    """
    格式化搜索结果为可读的文本形式

    Args:
        result: SearchResult 对象

    Returns:
        str: 格式化的搜索结果文本
    """
    if not result['results']:
        return f"未找到关于 '{result['query']}' 的搜索结果\n搜索来源: {result['source']}\n搜索时间: {result['timestamp']}"

    formatted_lines = [
        f"搜索关键词: {result['query']}",
        f"搜索来源: {result['source']}",
        f"返回结果数: {result['total_results']}",
        f"搜索时间: {result['timestamp']}",
        f"\n{'=' * 70}\n"
    ]

    for item in result['results']:
        formatted_lines.append(f"[{item['rank']}] {item['title']}")
        if item['snippet']:
            # 截断长摘要
            snippet = item['snippet']
            if len(snippet) > 400:
                snippet = snippet[:400] + "..."
            formatted_lines.append(f"摘要: {snippet}")
        if item['url']:
            formatted_lines.append(f"链接: {item['url']}")
        if item['relevance_score']:
            formatted_lines.append(f"相关性: {item['relevance_score']:.2f}")
        if item.get('published_date'):
            formatted_lines.append(f"发布日期: {item['published_date']}")
        formatted_lines.append("")

    return "\n".join(formatted_lines).strip()


def get_search_context(query: str, max_results: int = 5, api_key: str = None) -> str:
    """
    获取搜索上下文（Tavily 特定功能）

    Args:
        query: 搜索查询
        max_results: 最大结果数
        api_key: Tavily API key

    Returns:
        str: 搜索上下文
    """
    try:
        if not api_key:
            api_key = get_tavily_api_key()

        if not api_key:
            return "错误: 未找到 Tavily API key"

        if TavilyClient is None:
            return "错误: tavily-python 库未安装"

        client = TavilyClient(api_key=api_key)
        context = client.get_search_context(query=query, max_results=max_results)
        return context

    except Exception as e:
        error_msg = f"获取搜索上下文失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg


if __name__ == "__main__":
    # 测试代码
    try:
        test_input = json.dumps({
            "query": "Python 机器学习",
            "max_results": 3,
            "search_depth": "advanced"
        })
        result = search(test_input)
        print(result)
    except Exception as e:
        print(f"测试失败: {e}")
        sys.exit(1)
