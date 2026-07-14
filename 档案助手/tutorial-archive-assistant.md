# 教程：档案智能归档助手 — 文件归档

> 🧰 **附录 · 参考模板** ｜ ⏱ 约 30 min ｜ 🔑 文件读取 + 多模态读扫描件 + Shell 归档 + 子代理并行分类 + Skill
>
> 一份面向**本地档案/文件归档**的 Agent 参考骨架：把一堆杂乱文件（含扫描件、照片）读懂内容，按归档规则分类、重命名、归档到对应目录，并出归档清单。**始终保持原件内容不变。**
> 它是 NexAU **文件 + 多模态工具集**的经典用法示范——既能直接当归档助手用，也方便你起任何"跟本地文件/资料打交道"的新项目时照抄删改。
>
> 💡 建议先读完 ① 劳动法（Agentic RAG）、⑦ 公文写作（PDF/多模态）两篇，再来看本篇。

---

## 目录

- [场景与目标](#场景与目标)
- [总体架构一览](#总体架构一览)
- [设计要点 1：工具集——读取 / 多模态 / Shell](#设计要点-1工具集读取--多模态--shell)
- [设计要点 2：四个内置中间件](#设计要点-2四个内置中间件)
- [设计要点 3：子代理——只读批量分类并行委派](#设计要点-3子代理只读批量分类并行委派)
- [设计要点 4：Skill / 自定义工具 / MCP——领域知识与外部接入](#设计要点-4skill--自定义工具--mcp领域知识与外部接入)
- [设计要点 5：原件不可改，分类必有据](#设计要点-5原件不可改分类必有据)
- [完整目录结构](#完整目录结构)
- [动手实践：打包并部署到 NAC](#动手实践打包并部署到-nac)
- [裁剪自查清单](#裁剪自查清单)

---

## 场景与目标

**业务场景**：给一堆杂乱文件（文档、扫描件、照片），让 Agent 读懂、按规则分类、重命名归档、出清单。整条链路如下：

```
盘点 → 读懂(含扫描件/照片) → 加载归档规则(Skill) → 分类定级 → 重命名+归档(shell mv) → 出归档清单
```

它同时回答两个问题：

- **归档 agent 怎么搭？** —— 一个能直接跑的完整示例。
- **NexAU 文件/多模态工具怎么配、每个 binding 怎么写？** —— `agent.yaml` 字符串都对源码核实过，起新项目照着抄/删即可。

**每个能力在归档场景里都有真实用途**：

| 能力 | 在归档场景里干嘛 |
|------|------------------|
| **读取**（read_file / read_many_files） | 读懂文档内容，判断它是什么、该归哪类 |
| **多模态**（read_visual_file） | **读扫描件/照片**，识别其中文字与画面——档案归档关键 |
| **浏览/定位**（list_directory / glob / search_file_content） | 盘点待归档目录、按关键词找特定文件 |
| **写清单**（write_file） | 生成归档清单/索引（原名→新位置、门类、期限） |
| **Shell**（run_shell_command） | `mkdir` / `mv` / 重命名，实际把文件归位 |
| **自定义工具**（file_fingerprint） | 算文件指纹：sha256 识别重复扫描件 + 补元数据 |
| **子代理**（file_classifier） | 文件多时把不同批次**并行**委派分类，提速 |
| **Skill**（archive-rules） | 归档规则：分类体系、保管期限、命名规范 |
| **4 个中间件** | 长对话压缩、长工具输出截断、轮次/token 预算提示、运行时事实注入 |

> ⚠️ 归档要**保持原件原貌**，所以本助手**刻意不挂内容编辑工具**（replace/multiedit/apply_patch）——只读取、分类、重命名/移动、写清单。

---

## 总体架构一览

```
archive-assistant/                ← 含 nexau.json 的这一层就是要打包上传的根
├── nexau.json                    项目清单：声明 agent 入口、发布排除项
├── agent.yaml                    ⭐ 配置：工具 / 中间件 / 子代理 / MCP（全带注释）
├── systemprompt.md               归档指挥官：编排流程，归档规则下沉到 Skill
├── tools/                        11 个官方内置工具 schema + 1 个自定义工具 schema
├── custom_tools/
│   └── file_fingerprint.py       自定义工具：算文件指纹（识别重复 + 补元数据）
├── skills/
│   └── archive-rules/            归档规则 Skill（SKILL.md + references/分类·命名）
└── sub_agents/
    └── file_classifier/          只读文件分类子代理（自带 agent.yaml + tools/）
```

对照三个关键文件（见 [通用约定](../GETTING_STARTED.md#三个关键文件)）：`nexau.json` 说有哪些 Agent，`agent.yaml` 说 Agent 长什么样，`SKILL.md` 说 Agent 知道什么。

---

## 设计要点 1：工具集——读取 / 多模态 / Shell

每条工具都是 `name + yaml_path + binding` 三元组：

```yaml
tools:
  - name: read_visual_file
    yaml_path: ./tools/read_visual_file.tool.yaml
    binding: nexau.archs.tool.builtin.file_tools:read_visual_file
  - name: run_shell_command
    yaml_path: ./tools/run_shell_command.tool.yaml
    binding: nexau.archs.tool.builtin.shell_tools:run_shell_command
  # ... 读取 / 浏览 / 会话 同理
```

**binding 速查（已核实，可直接抄）**——都是短格式（函数在包 `__init__` re-export）：

```
# file_tools —— 读取 / 浏览 / 写清单
read_file            nexau.archs.tool.builtin.file_tools:read_file
read_visual_file     nexau.archs.tool.builtin.file_tools:read_visual_file      # 读扫描件/照片
read_many_files      nexau.archs.tool.builtin.file_tools:read_many_files
list_directory       nexau.archs.tool.builtin.file_tools:list_directory
glob                 nexau.archs.tool.builtin.file_tools:glob
search_file_content  nexau.archs.tool.builtin.file_tools:search_file_content
write_file           nexau.archs.tool.builtin.file_tools:write_file
# shell —— 实际执行归档动作（mkdir/mv/重命名）
run_shell_command    nexau.archs.tool.builtin.shell_tools:run_shell_command
# session_tools
write_todos          nexau.archs.tool.builtin.session_tools:write_todos
ask_user             nexau.archs.tool.builtin.session_tools:ask_user
save_memory          nexau.archs.tool.builtin.session_tools:save_memory
# 自定义工具（binding 指向 custom_tools/ 下的模块，不是 nexau 内置）
file_fingerprint     custom_tools.file_fingerprint:file_fingerprint
```

> **为什么没挂内容编辑工具**：档案要保持原貌，`replace` / `multiedit_tool` / `apply_patch` 是改文件**内容**的，归档用不到也不该用——归档只动文件名和位置（`mv`），不动内容。
>
> **`Agent`（子代理）和 `LoadSkill` 不用手动加到 `tools`**：声明了 `sub_agents:` / `skills:` 之后，框架会自动注入这两个工具。

每个 `*.tool.yaml`（工具 schema）都来自官方 [内置工具附录](../附录/内置工具附录.md)，原样复制到 `tools/` 即可。

---

## 设计要点 2：四个内置中间件

中间件是挂在执行前后的拦截器（见 [核心概念](../README.md#核心概念速览)）。本模板启用 **4 个 agent 级中间件**，import 路径与参数均已对源码核实。

| # | 中间件 | 作用 | 必填参数 |
|---|--------|------|---------|
| ① | ContextCompaction | 长对话自动压缩历史 | 无（`threshold` 默认 0.75） |
| ② | LongToolOutput | 单次工具输出过长时截断保留头尾 | 无 |
| ③ | RoundAndTokenReminder | 每轮注入"还剩几轮 / token 预算"提示 | `max_context_tokens` |
| ④ | RuntimeEnvironment | 把日期/工作目录等运行时事实注入 system | 无 |

import 路径统一前缀 `nexau.archs.main_sub.execution.middleware.`，**注意有 `execution` 这一层**（漏了会 import 报错，见 [常见问题](../GETTING_STARTED.md#常见问题)）。

启用段示例：

```yaml
middlewares:
  - import: nexau.archs.main_sub.execution.middleware.context_compaction:ContextCompactionMiddleware
    params:
      threshold: 0.6
  - import: nexau.archs.main_sub.execution.middleware.long_tool_output:LongToolOutputMiddleware
    params:
      max_output_chars: 10000
      head_lines: 50
      tail_lines: 30
```

> 归档场景尤其需要 ②：一次读很多文件、`list_directory` 一个大目录，输出常常很长，LongToolOutput 自动截断保留头尾，避免撑爆上下文。NexAU 还有 LLMFailover、AgentEvents 等中间件，需要时见源码 `nexau/archs/main_sub/execution/middleware/`。

---

## 设计要点 3：子代理——只读批量分类并行委派

声明 `sub_agents:` 后，主 Agent 就能用 `Agent` 工具把子任务委派出去（注意部署版 `sub_agents` 是**列表**，不是 dict）：

```yaml
sub_agents:
  - name: file_classifier          # name 即调用标识
    config_path: ./sub_agents/file_classifier/agent.yaml
```

> ⚠️ 调用子代理时用的是 `name`（`file_classifier`）。写错会报 `SubAgentNotFoundError`。

模板里的 `file_classifier` 是个**只读文件分类子代理**：只配了 `list_directory` / `read_file` / `read_visual_file` / `complete_task`，**刻意不给写文件/Shell**——分类归分类、归档动作归主 Agent。一次归档几百份文件时，把不同批次分给多个 file_classifier **并行判定门类**，能显著缩短耗时。这正是各样例强调的「只读子代理安全可并行」的思路。

子代理是一个完整的独立 Agent：自带 `agent.yaml`、`systemprompt.md`、`tools/`，结构和主 Agent 一模一样。

---

## 设计要点 4：Skill / 自定义工具 / MCP——领域知识与外部接入

### A. Skill：把归档规则喂给 Agent（Agentic RAG）

`skills/archive-rules` 是归档的「标准尺子」：分类体系（档案门类）、保管期限、命名规范、目录结构。Agent 归档前先 `LoadSkill` 加载它，按统一口径分类/命名、不凭主观感觉。大块规则放 `references/`（`classification.md`、`naming.md`），`SKILL.md` 做索引——这跟 ① 劳动法样例的层级化 Skill 思路一致。

> **落地必做**：把 `references/` 里的占位内容换成你**单位的真实归档规范**（门类、期限、命名格式），Agent 才好用。

### B. 自定义工具：内置工具不够时自己封装一个

内置工具覆盖不到的能力，用自定义工具补。三件套：`custom_tools/` 下 Python 实现 + `tools/*.tool.yaml` schema + `agent.yaml` 里 `binding: custom_tools.<模块>:<函数>` 注册。

本模板的 `file_fingerprint`（算文件指纹：sha256 识别重复扫描件 + 补元数据）就是个例子：

```yaml
  - name: file_fingerprint
    yaml_path: ./tools/file_fingerprint.tool.yaml
    binding: custom_tools.file_fingerprint:file_fingerprint
```

> ★ **要读 agent 正在处理的文件，自定义工具必须用沙箱 API，不能用裸 `open()`/`os.path`。**
> NAC 默认沙箱与运行时进程的文件系统是**隔离**的——`run_shell_command` / 内置文件工具在沙箱里操作，
> 而自定义工具（Python）跑在运行时进程；裸 `os.path` 读的是运行时进程的本地盘，**看不到沙箱里的工作文件**。
>
> 正确做法（和内置 `read_file` 一致）：函数声明 `agent_state` 参数（框架按签名**自动注入**，不写进 `input_schema`），
> 再 `from nexau.archs.tool.builtin._sandbox_utils import get_sandbox, resolve_path` → `sandbox = get_sandbox(agent_state)` → `sandbox.read_file(...)`。
>
> ```python
> def file_fingerprint(file_path: str, agent_state=None) -> dict:
>     sandbox = get_sandbox(agent_state)          # ← 通过 agent_state 拿沙箱
>     data = sandbox.read_file(resolve_path(file_path, sandbox), binary=True).content
>     ...
> ```
>
> 纯计算、不读 agent 工作文件的自定义工具则不需要 `agent_state`。需要密钥时用 `agent.yaml` 的 `extra_kwargs` 注入 `*, api_token: str`（不进 schema、模型看不到，完整示例见 ② **金融数据智能体** 样例）。

### C. MCP server（接已部署的工具服务）

和 ④ 数据库 MCP 样例同款，`mcp_servers` 声明外部 HTTP/stdio/sse 服务。档案场景可接**档案管理系统（DMS）/ 元数据库**（可选；纯本地归档时整段删除即可）：

```yaml
mcp_servers:
  - name: archive_dms
    type: http
    url: http://host.docker.internal:8000/mcp
    headers:
      Accept: application/json, text/event-stream
      Host: localhost:8000                  # 本机调试绕过 DNS rebinding 防护
    timeout: 30
```

不需要就整段删 `mcp_servers:`。

---

## 设计要点 5：原件不可改，分类必有据

归档最容易出问题的不是不会分，而是**乱动原件 / 乱归类**。systemprompt 把两条铁律写死：

- **原件不可改**：只分类、重命名、移动，**绝不修改文件内容**；归档前先确认是"实际移动"还是"只出方案"。
- **分类必有据**：每个门类/保管期限判定要对得上 `archive-rules` 的具体条目，拿不准用 `ask_user`，不硬归。

外加防御：批量 `mv`、覆盖同名文件等破坏性操作先 `ask_user` 确认；扫描件识别模糊、关键字段读不清的标 ⚠️ 转人工复核，不硬填。

> 换别的业务时按注释**按需删减**——不用的工具/中间件/MCP 直接删。工具越少，模型选错概率越低、上下文越省。

---

## 完整目录结构

打包上传前，确认根目录（含 `nexau.json`）结构如下：

```
archive-assistant/
├── nexau.json
├── agent.yaml
├── systemprompt.md
├── tools/                  *.tool.yaml × 12（11 内置 + 1 自定义）
├── custom_tools/file_fingerprint.py
├── skills/
│   └── archive-rules/{SKILL.md, references/{classification.md, naming.md}}
└── sub_agents/file_classifier/{agent.yaml, systemprompt.md, tools/}
```

`nexau.json` 指定入口与排除项：

```json
{
  "agents": { "archive_assistant": "agent.yaml" },
  "excluded": [".nexau/", ".env", "__pycache__/", "sub_agents/*/.nexau/"]
}
```

---

## 动手实践：打包并部署到 NAC

整体流程同 [通用约定](../GETTING_STARTED.md#如何把样例跑起来)：**打包 → 登录 NAC → 新建项目 → 上传 → 部署 → Playground 对话**。

### 1. 打包

仓库里已附打好的包 `archive-assistant.zip`，可直接用。要自己重打：

```bash
cd 档案助手/archive-assistant
zip -r ../archive-assistant.zip . -x "*.DS_Store" "*__pycache__*" "*.env" ".env"
```

> 压缩的是**含 `nexau.json` 的那一层**（即 `archive-assistant/` 内部），不要把外层目录也压进去。

### 2. 配模型环境变量

本模板用 `${env.LLM_MODEL}` / `${env.LLM_BASE_URL}` / `${env.LLM_API_KEY}` 读取模型配置。在 Playground 的「沙箱环境」里填入（配置方式见 [环境变量配置](../GETTING_STARTED.md#环境变量配置)）：

| 变量 | 说明 |
|------|------|
| `LLM_MODEL` | 模型 id；**做 agent 选支持 function calling 的型号** |
| `LLM_BASE_URL` | OpenAI 兼容网关地址（注意带 `/v1`） |
| `LLM_API_KEY` | 网关 API Key |

> 本模板**只需这 3 个变量**就能加载运行——核心归档功能纯本地、不联网。（`mcp_servers` 里的 `archive_dms` 只是可选示例：接档案系统时才用，不接就删掉那段，不影响加载。）

### 3. 上传部署 → Playground 验证

上传 `.zip` → 选环境部署 → 在 Playground 里让它干活，例如：

```
我有一批待归档文件在 ./待归档/ 目录，帮我读懂内容、按门类分类，给我一份归档方案清单。
```

> ⚠️ NexAU 对 `${env.变量名}` 是**文本级替换**（连注释里的也会被解析，变量没设就加载失败）；本模板已只保留 3 个必填的 `${env.LLM_*}`，其余示例都写成 `<占位符>`。

---

## 裁剪自查清单

部署自己的版本前，对照过一遍：

- [ ] **归档规则换成真实规范了吗？** —— `skills/archive-rules/references/` 的分类体系、保管期限、命名格式换成你**单位的真实规范**。
- [ ] **systemprompt 按需调整了吗？** —— 角色和流程贴合你的归档场景（是否允许实际移动、还是只出方案）。
- [ ] **工具裁剪了吗？** —— 用不到的内置工具删掉，留最小集。
- [ ] **中间件按需了吗？** —— ①②④ 一般留；③ 看是否需要轮次提示。
- [ ] **敏感文件排除了吗？** —— `.env` 在 `nexau.json` 的 `excluded` 里，打包命令也排除了它。
- [ ] **入口路径对吗？** —— `nexau.json` 的 `agents` 指向的 `agent.yaml` 路径相对 `nexau.json` 正确。

---

> 好的 Agent 不是把所有能力都打开，而是**只留对的那几个**。档案助手的核心就是「读懂、按规则分、保住原件」这条闭环。

---

← 返回 [Cookbook 主页](../README.md) ｜ [通用约定](../GETTING_STARTED.md)
