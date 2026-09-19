# 探针：验证 SSE 思考过程已降噪（thought 事件应精简为单行，不再贴技能内部日志/全文）
import json
import sys
import urllib.request

sys.path.insert(0, r"f:\1Github\vm")

url = "http://127.0.0.1:8001/api/chat/stream"
body = {
    "human_input": "深圳今天的天气怎么样，我的湿疹应该涂什药",
    "workflow_id": "probe-thought",
    "crm": "hn",
    "doctor_id": "1827196",
    "medical_record_no": "1881921",
}
req = urllib.request.Request(
    url, data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json; charset=utf-8"},
)
with urllib.request.urlopen(req, timeout=200) as resp:
    raw = resp.read().decode("utf-8", errors="replace")

events = []
for line in raw.splitlines():
    if not line.startswith("data:"):
        continue
    d = line[5:].strip()
    if d == "[DONE]":
        continue
    try:
        events.append(json.loads(d))
    except Exception:
        pass

thoughts = [e for e in events if e.get("type") == "thought"]
contents = [e for e in events if e.get("type") != "thought"]
print(f"thought 事件数: {len(thoughts)}；content 事件数: {len(contents)}")
print("===== thought 事件（应为简短摘要，无逐条日志/全文） =====")
for e in thoughts:
    print(f"[len={len(e['content'])}] {e['content'][:140]}")
print("===== content 末尾 500 字 =====")
joined = "".join(e.get("content", "") for e in contents)
print(joined[-500:])