# 教程：主动提问 — 让 Agent 在信息不足时问清楚，而不是猜

> 💬 横切能力 · 入门 ｜ 约 30 分钟 ｜ `ask_user` 工具 + `stop_tools` 停止语义 + 两轮 chat 的 API 对接

前面的样例里，Agent 拿到需求就直接开干。但真实业务里，用户的第一句话常常是不完整的：「帮我写份周报」——写哪周？给谁看？要不要含风险项？

Agent 面对信息缺口只有两种选择：**猜**，或者**问**。猜错了要返工，而且用户往往看不出它猜过；问清楚则多一次往返，但结果可靠。本篇用 NexAU 内置的 **`ask_user`** 工具实现后者，并讲清一个很容易踩的坑：**它必须配 `stop_tools` 才能真正「停下来等」**。

这是一个横切能力——任何已有 Agent 都能加上它。

---

## 前置条件

| 工具 / 资源 | 要求 | 验证 |
|---|---|---|
| NAC 账号 + 项目 | 已注册 [North Agent Cloud](https://nac.xiaobei.top) 并建了一个项目 | 浏览器登录 → 项目列表能看到 |
| 项目 AK/SK | 用于 API 调用（本篇最后一节） | 项目 → 配置 页可查看 |
| 模型 | 项目运行配置里已提供 `LLM_MODEL` | Playground 能正常对话 |

---

## 场景与目标

做一个「需求澄清助手」：用户提出一个不完整的请求时，它先问清关键信息再动手。

目标：

1. 信息不足时**主动提问**，而不是自行假设；
2. 提问后**真的停下来等**用户回答，而不是自问自答继续往下跑；
3. 拿到回答后继续完成任务，不重复追问同一件事；
4. 能通过 **API 完成整个交互链路**，供程序化集成。

---

## 项目结构

```text
主动提问/
└── ask_user_agent/               ← 上传到 NAC 的完整 artifact
    ├── nexau.json
    ├── agent.yaml                ← 核心：tools 挂 ask_user + stop_tools 声明
    ├── systemprompt.md           ← 何时该问、怎么问、拿到回答后做什么
    └── tools/
        └── ask_user.tool.yaml    ← 工具声明（内置工具，直接复制即可）
```

本样例**只挂了 `ask_user` 一个工具**，便于观察它的完整行为。

---

## 设计要点 1：`stop_tools` 不是可选项

这是本篇最重要的一行配置：

```yaml
tools:
  - name: ask_user
    yaml_path: ./tools/ask_user.tool.yaml
    binding: nexau.archs.tool.builtin.session_tools:ask_user

stop_tools: [ask_user]      # ← 少了这行，整个样例的行为就是错的
```

`ask_user` 是一个**停止型工具（stop tool）**，语义是「把问题抛出去，然后停下本轮运行，等用户回答」。

**不写进 `stop_tools` 会怎样**：Agent 调用 `ask_user` 之后不会停，它会看到工具返回的
「正在等待用户回答」，然后**自己编一个答案继续往下跑**。

⚠️ 这个错误**不会报错、不会告警**，产出看起来完全正常——只是那个「用户的回答」是模型编的。
平台的制品静态规范因此把这条列为硬性要求：挂了 `ask_user` 就必须同时声明 `stop_tools`。

配置正确时，事件流里的工具返回会带上标记：

```json
{"content": "Asking user questions, waiting for user answers...", "_is_stop_tool": true}
```

看到 `_is_stop_tool: true` 就说明这条链路是对的。

---

## 设计要点 2：三种问题类型，选错会被直接拒绝

`ask_user` 一次最多问 4 个问题，每个问题都要显式指定 `type`：

| `type` | 什么时候用 | 必填字段 |
|---|---|---|
| `choice` | 有 2–4 个明确的候选项；多选加 `multiSelect: true` | `options`（每项含 `label` + `description`） |
| `text` | 答案是自由文本（路径、名称、数字、描述…） | `placeholder`（输入提示） |
| `yesno` | 是非确认 | — |

⚠️ **没有可枚举的选项时必须用 `text`**。发一个 `options` 为空的 `choice` 会被直接拒绝——
这是最常见的调用错误。样例的 `systemprompt.md` 里专门写了这条规则来约束模型。

`header` 字段值得单独说：它是问题的短标题（≤32 字），既作为标签展示，**也是用户回答时的引用前缀**（见设计要点 4）。所以要写得简短、可辨识，例如「输出格式」「目标文件」，而不是「请问您希望的输出格式是什么呢」。

---

## 设计要点 3：约束「什么时候不该问」比「什么时候该问」更重要

只告诉模型「信息不足就提问」，结果往往是它对什么都要确认一遍，交互变得啰嗦。
样例的 systemprompt 用一张表把两个方向都写死：

| 情况 | 该不该问 |
|---|---|
| 缺少执行所必需的关键信息 | ✅ 问 |
| 有多个合理选项，选哪个会显著改变结果 | ✅ 问 |
| 用户明确要求你问他 | ✅ 问 |
| 信息已经够了，只是想再确认一遍 | ❌ 不问，直接做 |
| 能从上下文合理推断出来 | ❌ 不问，做完说明你的假设 |

并补一条数量约束：**一次最多问 4 个，且只问真正影响结果的**——宁可问一个关键问题，不要凑四个无关紧要的。

---

## 上传部署

```bash
cd 主动提问
zip -r ask_user_agent.zip ask_user_agent -x "*/__pycache__/*" "*.pyc"
```

浏览器上传到 NAC 项目 → 部署到某个环境（如 `test`）。

需要在项目**运行配置**里提供 `LLM_MODEL`（模型名要带 `provider/` 前缀）。

---

## Playground 验证

输入一个信息不完整的请求：

```
帮我写一份周报
```

预期：Agent 不直接动笔，而是弹出提问卡片，例如「周报范围：这份周报覆盖哪个时间段？」。

在卡片里填写答案并提交，Agent 收到后继续完成任务。

**如果 Agent 没有提问、直接编了一份周报**，检查两处：`agent.yaml` 里 `stop_tools` 是否声明、
`systemprompt.md` 里是否说清了什么情况该问。

---

## 通过 API 调用

Playground 里点选就能回答，但程序化集成时要自己处理这一来一回。完整链路是**两轮 chat**，
用**同一个 `session_id`**。

### ① 创建会话

```bash
curl -u "$AK:$SK" -H 'Content-Type: application/json' \
  -X POST "$BASE/agent-api/sessions" \
  -d '{"distinct_id":"user-123"}'
# => {"session_id":"sess_xxx", ...}
```

### ② 发起对话，Agent 提问

```bash
curl -N -u "$AK:$SK" -H 'Content-Type: application/json' \
  -X POST "$BASE/agent-api/chat" \
  -d '{
    "environment": "test",
    "session_id": "sess_xxx",
    "distinct_id": "user-123",
    "stream": true,
    "messages": [{"role":"user","content":"帮我写一份周报"}]
  }'
```

问题内容分布在 SSE 流的两类事件里，**需要自己拼接**：

| 事件 | 作用 |
|---|---|
| `TOOL_CALL_START` | `tool_call_name` 为 `ask_user` 时，说明 Agent 在提问 |
| `TOOL_CALL_ARGS` | 多条，每条带一个 `delta` 片段，**按顺序拼起来**才是完整的问题 JSON |

拼接结果：

```json
{"questions": [{
  "header": "周报范围",
  "question": "这份周报覆盖哪个时间段？",
  "type": "text",
  "placeholder": "例如：本周一到周五",
  "options": [],
  "multiSelect": false
}]}
```

### ③ 用第二轮 chat 把答案发回去

内容格式是 **`标题: 答案`**，其中标题就是上一步的 `header`：

```bash
curl -N -u "$AK:$SK" -H 'Content-Type: application/json' \
  -X POST "$BASE/agent-api/chat" \
  -d '{
    "environment": "test",
    "session_id": "sess_xxx",
    "distinct_id": "user-123",
    "stream": true,
    "messages": [{"role":"user","content":"周报范围: 本周一到周五"}]
  }'
```

各类型的答案怎么填：

| `type` | 答案格式 | 示例 |
|---|---|---|
| `text` | 任意文本 | `周报范围: 本周一到周五` |
| `choice`（单选） | 选中项的 `label` 原文 | `输出格式: Markdown` |
| `choice`（多选） | 多个 `label`，逗号分隔 | `关注模块: 前端, 数据` |
| `yesno` | `是` / `否` | `是否包含风险项: 是` |

### 完整示例（Python，仅标准库）

```python
import json, base64, urllib.request, ssl

BASE, AK, SK, ENV = "https://<your-nac-host>", "ak_xxx", "sk_xxx", "test"
AUTH = "Basic " + base64.b64encode(f"{AK}:{SK}".encode()).decode()

def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": AUTH},
        method="POST")
    return urllib.request.urlopen(req, timeout=300, context=ssl.create_default_context())

def chat(sid, did, text):
    """发一轮对话，返回 (Agent 提出的问题 or None, Agent 的文本回复)"""
    resp = post("/agent-api/chat", {
        "environment": ENV, "session_id": sid, "distinct_id": did,
        "stream": True, "messages": [{"role": "user", "content": text}]})
    args, out, asking = [], [], False
    for raw in resp:
        line = raw.decode("utf-8", "ignore").strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line.startswith(":"):     # 空行与心跳注释要跳过
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "TOOL_CALL_START" and ev.get("tool_call_name") == "ask_user":
            asking = True
        elif t == "TOOL_CALL_ARGS":
            args.append(ev.get("delta", ""))
        elif t == "TEXT_MESSAGE_CONTENT":
            out.append(ev.get("delta", ""))
    question = json.loads("".join(args))["questions"][0] if asking and args else None
    return question, "".join(out).strip()

did = "user-123"
sid = json.loads(post("/agent-api/sessions", {"distinct_id": did}).read())["session_id"]

q, reply = chat(sid, did, "帮我写一份周报")

MAX_ROUNDS = 5          # Agent 可能连续问几轮；设上限，避免异常情况下无限往返
for _ in range(MAX_ROUNDS):
    if not q:
        break
    print(f"[{q['header']}] {q['question']}")
    if q.get("options"):
        print("  可选:", ", ".join(o["label"] for o in q["options"]))
    ans = input("你的回答: ")
    q, reply = chat(sid, did, f"{q['header']}: {ans}")
else:
    print(f"(已达 {MAX_ROUNDS} 轮提问上限，停止)")

print("Agent:", reply)
```

### 关于 `/agent-api/user-message`

平台还有一个 `POST /agent-api/user-message` 接口，它也能回答 `ask_user`，
但要求**这一轮 run 仍在运行**。而 `ask_user` 是停止型工具，一返回 run 就结束，
可用窗口只有约一秒且不固定——**真人来不及读题、思考、输入**。超时会收到：

```json
{"detail": "No running agent for session_id sess_xxx"}
```

⇒ **交互式场景一律用上面的两轮 chat 方式。** `user-message` 更适合另一类用途：
在 Agent 执行长任务期间给它**补充信息**，那时 run 还活着，什么时候发都行。

---

## 把主动提问装到你自己的 Agent 上

三步，不需要改任何业务逻辑：

1. 把 `tools/ask_user.tool.yaml` 复制进你的 artifact；
2. `agent.yaml` 的 `tools` 里加上这个工具，**并把 `ask_user` 写进 `stop_tools`**；
3. 在 systemprompt 里写清楚「什么时候该问、什么时候不该问」——这一步决定交互质量。

第 3 步最容易被略过，但它决定了 Agent 是「问得恰到好处」还是「什么都要确认一遍」。

---

## 注意事项

| 现象 | 原因 |
|---|---|
| Agent 提完问不停，自己编了个答案 | `agent.yaml` 里漏了 `stop_tools: [ask_user]` |
| 调用被拒绝 | 发了 `options` 为空的 `choice`；没有候选项时应该用 `type: text` |
| `404 VERSION_TAG_NOT_FOUND: 'production'` | API 请求体漏传 `environment`，会默认回落到 `production` |
| Agent 答非所问 | 第二轮没复用同一个 `session_id`，或回答漏了 `标题:` 前缀 |
| `409 SESSION_BUSY` | 同一会话上还有未结束的 run；等它结束或先调 `POST /agent-api/stop` |
| Agent 问得太啰嗦 | systemprompt 里只写了「该问什么」，没写「不该问什么」 |

---

## 延伸阅读

- [开始之前：样例项目的通用约定](../GETTING_STARTED.md) — 目录结构、上传流程、环境变量
- [敏感词过滤](../敏感词过滤/) — 另一个横切能力：内容安全护栏
- NexAU 官方仓库：https://github.com/nex-agi/NexAU
