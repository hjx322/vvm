# 如果项目由于环境问题运行不起来可以参考这个文件
当前仓库由多人同步开发，存在不同的模块，因此开发环境可能比较复杂

在此留下一份基础的UV环境，如果涉及到torch等相关内容的时候可以把 **pyproject.toml** 中的包，替换为以下内容:

```
    "dashscope==1.23.2",
    "langchain==0.3.24",
    "langchain-community==0.3.23",
    "langchain-milvus==0.1.6",
    "langchain-openai==0.3.24",
    "langgraph==0.4.8",
    "langgraph-checkpoint-mysql==2.0.15",
    "pydantic-settings==2.9.1",
    "pymilvus==2.5.8",
    "pymysql==1.1.1",
    "pyyaml==6.0.3",
    "requests>=2.32.5",
```

此外，如果openskills运行不起来需要，参考github上openskills的相关安装，或本目录下openskills的相关文档