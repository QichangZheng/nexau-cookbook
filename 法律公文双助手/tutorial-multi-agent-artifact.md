# 教程：一个制品，多个智能体 — MultiAgent 制品怎么组织

> 🔗 架构模式 · 进阶 ｜ 约 45 分钟 ｜ `nexau.json` 注册多 Agent + chat 请求用 `agent` 字段选择

前面每个样例都是**一个制品一个 Agent**。但一个部门的服务往往不止一件事：市民既可能来咨询劳动纠纷，也可能要求出一份正式公文。这两件事的知识、工具、提示词完全不同，**塞进同一个 Agent 会互相干扰**；分成两个制品又要维护两套部署、两套环境变量、两组 AK/SK。

NexAU 的答案是 **MultiAgent 制品**：一个制品里放多个**平级**的 Agent，共享一次部署，调用时按名字选。

本篇把 Cookbook 里已有的 [劳动法问答](../劳动法问答/) 和 [公文写作](../公文写作/) **原样装进一个制品**，你会看到这件事有多轻——两个 Agent 的目录几乎不用改。

---

## 前置条件

| 工具 / 资源 | 要求 | 验证 |
|---|---|---|
| 读过两个来源样例 | [劳动法问答](../劳动法问答/)、[公文写作](../公文写作/) | 知道它们各自解决什么 |
| NAC 账号 + 项目 | 已注册 [North Agent Cloud](https://nac.xiaobei.top) 并建了项目 | 浏览器登录能看到项目 |
| 项目 AK/SK | API 调用用（最后一节） | 项目 → 配置 页可查看 |

---

## 核心：平级，不是主从

这是最容易理解错的一点。MultiAgent 制品里的多个 Agent 是**平级**的：

- ❌ 不是「一个主 Agent 调度几个子 Agent」——那是 `sub_agents` 委派，是另一种模式
- ✅ 是「一个制品里并排放着几个独立 Agent」，谁也不管谁，**由调用方决定这次用哪个**

```text
法律公文双助手/
└── legal_govdoc_agent/               ← 上传到 NAC 的完整 artifact
    ├── nexau.json                    ← 同时注册两个 Agent
    ├── labor_law/                    ← Agent 一，完全独立
    │   ├── agent.yaml
    │   ├── systemprompt.md
    │   ├── tools/
    │   └── skills/labor-lawyer-cn/
    └── govdoc/                       ← Agent 二，完全独立
        ├── agent.yaml
        ├── systemprompt.md
        ├── tools/
        └── skills/{pdf_to_md,docx-report,govdoc-writing}/
```

制品根下**没有** `agent.yaml`、**没有** `systemprompt.md` —— 每个 Agent 的配置都在自己目录里。

---

## 设计要点 1：`nexau.json` 是唯一需要写的"新"文件

```json
{
  "agents": {
    "labor_law_qa": "labor_law/agent.yaml",
    "govdoc_writer": "govdoc/agent.yaml"
  },
  "excluded": [".nexau/", ".env", "__pycache__/", "*.pyc", ".DS_Store"]
}
```

- **key** = Agent 名字，调用时用它指定（必须与该 `agent.yaml` 里的 `name` 一致）
- **value** = 该 Agent 配置文件的路径，**相对制品根**

想再加一个 Agent？把它的目录放进来，在这里加一行即可。

---

## 设计要点 2：两个 Agent 的目录几乎不用改

这是 MultiAgent 最省力的地方：每个 Agent 目录**整体搬进来就行**。

因为 `agent.yaml` 里的 `system_prompt` / `tools[].yaml_path` / `skills[]` 的相对基准，
是**它自己所在的目录**，不是制品根。所以：

```yaml
# labor_law/agent.yaml —— 与它独立运行时一字不差
system_prompt: ./systemprompt.md
tools:
  - name: read_file
    yaml_path: tools/read_file.tool.yaml
    binding: nexau.archs.tool.builtin.file_tools:read_file
skills:
  - ./skills/labor-lawyer-cn
```

本样例从两个来源样例搬运时，`agent.yaml` 的业务配置一行没改，只做了两处与 MultiAgent 无关的规范化：

| 改动 | 原因 |
|---|---|
| systemprompt 补一句 skills 根目录声明 | 平台制品规范的硬要求（正则匹配） |
| 去掉 `base_url` / `api_key` / `api_type` / `stream` | 这几个字段在 NAC 上会被平台覆盖，写了不生效，作为教学样例去掉噪声 |

> 顺带修了来源样例的一个小瑕疵：劳动法那份 `agent.yaml` 里 `max_iterations` 写了两次（300 与 50），
> YAML 里后者覆盖前者，等于前一行无效。

---

## 设计要点 3：Agent 之间完全隔离

同一个制品里的两个 Agent，**运行时互不相干**：

| 维度 | 是否共享 |
|---|---|
| 上下文 / 会话历史 | ❌ 各自独立 |
| skills 与知识库 | ❌ 各自加载自己声明的 |
| 工具集 | ❌ 各自声明 |
| 提示词 | ❌ 各自的 systemprompt |
| 制品文件 / 部署 / 版本 | ✅ 同一份 |
| 环境变量（`LLM_MODEL` 等） | ✅ 同一套 |

这正是 MultiAgent 的价值：**打包与部署合一，运行时隔离**。改劳动法知识库不会影响公文写作；
公文写作的长上下文也不会挤占劳动法问答的窗口。

⚠️ 唯一真正共享的命名空间是 **skill 目录 basename**：skills 部署到沙箱后位于
`/home/user/.skills/<目录 basename>`。本样例的四个 skill（`labor-lawyer-cn` / `pdf_to_md` /
`docx-report` / `govdoc-writing`）互不重名；如果两个 Agent 各有一个叫 `utils` 的 skill，会撞车。

合并前先跑一遍：

```bash
find . -path "*/skills/*" -maxdepth 3 -type d | xargs -n1 basename | sort | uniq -d
# 有输出就说明重名，需要先改目录名
```

---

## 设计要点 4：调用时必须指定 `agent`

单 Agent 制品的 chat 请求不需要说用谁。MultiAgent **必须说**：

```json
{
  "environment": "test",
  "agent": "labor_law_qa",          ← 就是 nexau.json 里的 key
  "session_id": "sess_xxx",
  "messages": [{"role": "user", "content": "公司裁我没给补偿合法吗？"}]
}
```

⚠️ **不传 `agent` 字段会直接 400。** 这是 MultiAgent 制品最常见的接入报错——
平台控制台「复制 API 脚本」生成的示例**不含** `agent` 字段，照抄会踩。

换一个 Agent 只需要改这一个字段：

```json
{"agent": "govdoc_writer", "messages": [{"role": "user", "content": "根据这份材料写一份情况报告"}]}
```

> **会话是按 `session_id` 隔离的**：想让两个 Agent 各自记住自己的对话，就给它们各用一个 session。
> 同一个 session 里换 Agent 会把上一个 Agent 的历史带过去，通常不是你想要的。

---

## 上传部署

```bash
cd 法律公文双助手
zip -r legal_govdoc_agent.zip legal_govdoc_agent -x "*/__pycache__/*" "*.pyc" "*.DS_Store"
```

浏览器上传到 NAC → 部署到某个环境。**一次部署，两个 Agent 同时可用。**

运行配置里提供 `LLM_MODEL`（两个 Agent 共用）。

---

## Playground 验证

Playground 的 Agent 下拉框里会出现**两个**条目（`labor_law_qa` / `govdoc_writer`）。

**① 选 `labor_law_qa`**

```
公司以「组织架构调整」为由裁掉我，只给了一个月工资，合法吗？
```

预期：给出带法条依据的分析与赔偿测算思路。

**② 切到 `govdoc_writer`**，上传一份材料 PDF：

```
根据这份材料写一份情况报告
```

预期：解析 PDF → 按公文格式生成 .docx，并给出文件路径。

**③ 验证隔离**：在 ② 里问劳动法问题，它不会引用 `labor-lawyer-cn` 的知识——
那个 skill 没挂在它身上。这正是隔离生效的证据。

---

## API 调用

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

def ask(agent_name, text, session_id=None, did="user-123"):
    """向指定 Agent 提问；不同 Agent 建议各用各的 session"""
    if session_id is None:
        session_id = json.loads(post("/agent-api/sessions", {"distinct_id": did}).read())["session_id"]
    resp = post("/agent-api/chat", {
        "environment": ENV,
        "agent": agent_name,          # ← MultiAgent 制品必须指定，漏了会 400
        "session_id": session_id,
        "distinct_id": did,
        "stream": True,
        "messages": [{"role": "user", "content": text}]})
    out = []
    for raw in resp:
        line = raw.decode("utf-8", "ignore").strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line.startswith(":"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "TEXT_MESSAGE_CONTENT":
            out.append(ev.get("delta", ""))
    return session_id, "".join(out).strip()

# 两个 Agent 各用一个会话，互不干扰
_, legal = ask("labor_law_qa",  "工作 3 年 2 个月、月薪 12000，违法解除该赔多少？")
print("法律咨询:", legal)

_, doc = ask("govdoc_writer", "根据 /home/user/uploads/材料.pdf 写一份情况报告")
print("公文写作:", doc)
```

---

## 什么时候用 MultiAgent，什么时候用 `sub_agents`

两者不是一回事，别混：

| | MultiAgent（本篇） | `sub_agents` 委派 |
|---|---|---|
| 关系 | **平级**，互不知道对方存在 | **主从**，主 Agent 委派子 Agent |
| 谁决定用哪个 | **调用方**（`agent` 字段） | **主 Agent**（运行时判断） |
| 适合 | 几件**并列**的业务，用户自己知道要办哪件 | 一件事需要**拆成几步**由不同专家接力 |
| 典型场景 | 服务大厅的多个窗口 | 一条流水线上的多道工序 |

需要「用户说一句话，系统自动判断该派谁」时，才用 `sub_agents`；
用户自己能选、或者前端有入口区分时，MultiAgent 更简单——**少一层 LLM 判断，少一次出错机会**。

---

## 注意事项

| 现象 | 原因 |
|---|---|
| chat 请求 400 | 没传 `agent` 字段。MultiAgent 制品必须指定 |
| 指定的 Agent 找不到 | `agent` 的值要与 `nexau.json` 的 key 一致，也要与该 `agent.yaml` 的 `name` 一致 |
| 两个 Agent 的知识串了 | skill 目录 basename 重名，撞在同一个沙箱路径 |
| 切换 Agent 后它知道上一个的对话 | 复用了同一个 `session_id`；各用各的 session |
| 静态校验报「顶层缺 agent.yaml / systemprompt.md」 | MultiAgent 形态的固有现象——校验器的 `required_top_level_files` 假定单 Agent 扁平布局。**逐个 Agent 目录单独校验**才是有效判据 |

最后一条值得展开。平台的静态校验器只读**顶层** `agent.yaml`，MultiAgent 制品根本没有那个文件。
正确做法是给每个 Agent 目录临时补一份 `nexau.json` 单独校验：

```bash
for a in */; do
  [ -f "$a/agent.yaml" ] || continue
  n=$(grep -m1 '^name:' "$a/agent.yaml" | awk '{print $2}')
  cp -r "$a" /tmp/check && printf '{"agents":{"%s":"agent.yaml"}}' "$n" > /tmp/check/nexau.json
  # 用平台校验器检查 /tmp/check
  rm -rf /tmp/check
done
```

> **已知项**：`govdoc` 的两个 skill（`docx-report` / `govdoc-writing`）的 `references/` 缺索引文件，
> 单独校验会报 4 条 `index_missing` / `directory_index_missing`。这继承自来源样例
> [公文写作](../公文写作/)，本样例未改动其知识库组织。

---

## 延伸阅读

- [劳动法问答](../劳动法问答/) — 本样例的 Agent 之一：Agentic RAG
- [公文写作](../公文写作/) — 本样例的 Agent 之二：多 Skill + docx 渲染
- [开始之前：样例项目的通用约定](../GETTING_STARTED.md)
