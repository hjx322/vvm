# 探针：端到端对话验证（Python urllib 直连，避免 PowerShell 解码乱码）
# 用法：.venv\Scripts\python.exe scripts\probe_e2e_chat.py
import json
import urllib.request
import sys

sys.path.insert(0, r"f:\1Github\vm")

BASE = "http://127.0.0.1:8001/api/chat"


def ask(question, workflow_id):
    body = json.dumps(
        {"human_input": question, "workflow_id": workflow_id, "crm": "hn", "doctor_id": "1827196"}
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE, data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=150) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    reply = json.loads(raw).get("reply", "")
    return reply


if __name__ == "__main__":
    q1 = "深圳今天天气怎么样"
    print("######## 问题1（纯天气）:", q1)
    print(ask(q1, "probe-e2e-w1"))
    print()

    q2 = "深圳今天的天气怎么样，我的湿疹应该涂什药"
    print("######## 问题2（天气+湿疹）:", q2)
    print(ask(q2, "probe-e2e-w2"))