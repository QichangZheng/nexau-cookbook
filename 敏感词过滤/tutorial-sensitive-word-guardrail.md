# 教程：敏感词内容安全护栏 — 给任何政务 Agent 加一道合规闸门

> 🛡️ 横切能力 · 入门 ｜ 约 30 分钟 ｜ SensitiveWordMiddleware + 输入/输出/工具结果三路拦截

前面 10 个样例解决的都是「Agent 怎么把业务做对」；这一篇解决的是「Agent 不能说错话」。政务场景上线前绕不开内容安全评审：用户输入了违规内容怎么办？模型万一输出了不该说的词怎么办？Agent 读取的材料里夹带敏感内容怎么办？

本篇用 NexAU 的 **敏感词中间件（`SensitiveWordMiddleware`，RFC-0027）** 回答这三个问题——不改一行业务代码，在 `agent.yaml` 里加一段 `middlewares` 声明，就给任意已有 Agent 套上一层确定性的内容安全护栏。

---

## 前置条件

| 工具 / 资源 | 要求 | 验证 |
|---|---|---|
| NexAU 版本 | 包含 **RFC-0027 敏感词中间件** 的版本 | `python -c "from nexau.archs.main_sub.execution.middleware.sensitive_word import SensitiveWordMiddleware"` 不报错 |
| NAC 账号 + 项目 | 已注册 [North Agent Cloud](https://nac.xiaobei.top) 并建了一个项目 | 浏览器登录 → 项目列表能看到 |
| 敏感词词库 | 样例自带「涉枪涉爆」完整类别词库 + 3 个演示词；接入其他类别见设计要点 3 | — |

> ⚠️ **版本提示**：截至本文撰写，敏感词中间件在 NexAU 的 `feat/sensitive-word-middleware` 分支（RFC-0027），尚未合入 main / 发版。上传 NAC 前先用上面那行 import 在目标 runtime 版本里验证一下；如果报 `ModuleNotFoundError`，说明所部署的 NexAU 还不带这个中间件。

---

## 场景与目标

假设你已经有一个政务咨询 Agent（任何一个前面样例都行），内容安全评审提出三条硬要求：

1. 市民输入中出现敏感词（涉枪涉爆、贪腐举报关键词等）时，**请求不能进模型**，要按统一话术答复；
2. 模型输出万一带出敏感词，**不能返回给用户**；
3. Agent 通过工具读取的材料（来信、留言、附件）里如果含敏感词，**不能被模型拿去继续生成**。

目标是构建一个带内容安全护栏的政务咨询 Agent：

1. 词库按「类别 = 文件」组织，随 artifact 一起部署，安全团队可独立维护；
2. 上述三条链路全部拦截，命中即终止本次 run，返回统一拒绝文案；
3. 拦截行为可观测：结构化事件 + 日志，方便审计与复盘。

---

## 项目结构

```text
敏感词过滤/
└── sensitive_word_agent/                  ← 上传到 NAC 的完整 artifact
    ├── nexau.json
    ├── agent.yaml                         ← 核心：middlewares 里声明敏感词中间件
    ├── systemprompt.md                    ← 普通的政务咨询助手 prompt（与安全护栏解耦）
    ├── tools/
    │   └── read_file.tool.yaml            ← 内置 read_file,用于演示工具结果拦截
    ├── lexicon/                           ← 敏感词词库:每个 .txt 文件名 = 类别,一行一个词
    │   ├── 涉枪涉爆词库.txt                ← 完整类别词库(取自 konsheng/Sensitive-lexicon)
    │   ├── 民生词库.txt                    ←（演示）打人
    │   ├── 贪腐词库.txt                    ←（演示）腐败
    │   ├── 本单位自定义词库.txt            ←（演示）内部测试代号
    │   └── README.md                      ← 词库来源与裁剪说明(不会被中间件加载)
    └── materials/
        └── 网民留言-20260601.txt          ← 含敏感词的虚构材料,用于触发工具结果拦截
```

关键文件：

| 文件 | 职责 |
|---|---|
| `agent.yaml` | 声明 `SensitiveWordMiddleware` 及其参数（词库路径、拦截开关） |
| `lexicon/*.txt` | 词库本体。「涉枪涉爆」为完整类别词库，其余类别只放演示词 |
| `materials/网民留言-20260601.txt` | 演示材料，内容含「腐败」一词，用于验证工具结果拦截链路 |

> 「民生 / 贪腐」两类的演示词（打人 / 腐败）与 NexAU 仓库 `examples/sensitive_word/` 的官方示例一致，方便对照调试；「涉枪涉爆」类直接收录上游完整词库（官方示例词「出售雷管」也在其中），来源与裁剪原则见 [`lexicon/README.md`](./sensitive_word_agent/lexicon/README.md)。

---

## 设计要点 1：为什么用 Middleware，而不是在 Prompt 里写「不要谈 XX」

把内容安全写进 system prompt（"遇到敏感话题请拒绝回答"）是最常见的做法，也是评审最不认的做法：

- **不确定**：模型对 prompt 约束的遵循是概率性的，换个问法就可能绕过；
- **不可审计**：拦没拦、为什么拦，没有结构化记录；
- **管不到输入侧**：违规输入照样进模型、照样计费，prompt 只能影响输出。

Middleware 是挂在 Agent 执行引擎上的**确定性拦截器**（参见 [README 核心概念速览](../README.md#核心概念速览)）。敏感词中间件内置 Aho-Corasick 多模式匹配自动机（纯 Python 实现，无第三方依赖），命中就是命中：

```text
用户输入 ──► before_model 扫描 ──┐
                                ├─ 命中 ──► 终止 run,返回统一拒绝文案
工具结果 ──► before_model 扫描 ──┤          + 发 ContentBlockedEvent + 记日志
                                │
模型输出 ──► after_model  扫描 ──┘
   │
   └─ 未命中 ──► 正常继续
```

prompt 约束和词库拦截不互斥：prompt 负责「引导模型得体」，中间件负责「兜底一定拦住」。

---

## 设计要点 2：三路拦截的时机

| 来源 | 钩子 | 时机 | 效果 |
|---|---|---|---|
| 用户 / 系统 / 框架输入 | `before_model` | 调模型**前**短路 | 违规请求不发给 LLM（不产生调用费用） |
| 工具结果（`Role.TOOL`） | `before_model` | 工具执行后的**下一轮**模型调用前 | 工具读到的敏感内容不会进入模型上下文继续生成 |
| 模型输出 | `after_model` | 整段响应聚合后、执行工具前 | 违规输出不会作为最终回复返回 |

两个值得注意的细节：

- **工具结果是在「下一轮」被拦的**。事件序列是 `TOOL_CALL_RESULT → ContentBlockedEvent(source=input) → RUN_ERROR`：工具本身会执行完（材料确实被读了），拦截发生在结果要喂给模型的那一刻。
- **流式输出下，输出侧是"整段流完才拦"**。违规 chunk 可能已经流到前端，最终回复会被替换为拒绝文案。如果前端逐字渲染流式内容，需要在收到 `RUN_ERROR` / `ContentBlockedEvent` 时清掉已渲染的部分。输入侧不受此影响。

---

## 设计要点 3：词库随 artifact 部署，路径必须是绝对路径

`agent.yaml` 的核心配置就这一段：

```yaml
middlewares:
  - import: nexau.archs.main_sub.execution.middleware.sensitive_word:SensitiveWordMiddleware
    params:
      lexicon_dir: /agent/lexicon   # artifact 内的 lexicon/ 目录,部署后挂载到 /agent/lexicon
      case_sensitive: false         # 英文敏感词建议 false;中文不受影响
      block_input: true             # 拦用户输入(含 tool result)
      block_output: true            # 拦模型输出
      raise_on_block: false         # false=返回拒绝文案;true=抛 SensitiveContentBlockedError
```

**为什么是 `/agent/lexicon`？** NAC 部署后 artifact 出现在沙箱的 `/agent` 目录下，runtime 进程同样可见——这是平台的 artifact root 约定（详见[达梦样例](../达梦数据库/tutorial-dameng-sql-agent.md)的说明，企业问数的 `db_path: /agent/enterprise.sqlite` 也是同一约定）。注意**不要写相对路径**：中间件按进程 CWD（`/home/user`）解析相对路径，不是按 `agent.yaml` 所在目录。更要命的是**路径写错不会报错**——中间件对不存在的目录静默跳过、加载 0 词，护栏完全失效但 Agent 一切如常。部署后务必在启动日志里核对 `[SensitiveWordMiddleware] loaded N words`，N 必须与你的词库规模一致。

**词库组织**：`lexicon_dir` 下每个 `.txt` 文件名就是类别名，一行一个词。拒绝文案和审计事件里都会带上类别，所以按业务含义拆文件（民生 / 涉枪涉爆 / 贪腐 / 本单位自定义……），而不是塞一个大文件。

除了目录方式，中间件还支持另外三种词库来源，可叠加：

```yaml
params:
  lexicon_file: /agent/words.txt          # 单文件:文件名=类别
  lexicon_words: ["打人", "腐败"]          # 显式词表:统一归入 "explicit" 类
  extra_words: ["内部代号A", "项目X"]      # 在已有词库基础上追加:归入 "extra" 类
```

什么都不传时，中间件使用 NexAU **包内自带的 3 词演示词库**（同样取自下文的 konsheng 词库）——只够跑通 demo，生产必须换成自己的完整词库。

**生产词库从哪来？** 推荐从开源中文敏感词库 [konsheng/Sensitive-lexicon](https://github.com/konsheng/Sensitive-lexicon)（MIT 许可，3.6k+ star，持续更新）起步。它的 `Vocabulary/` 目录与本中间件的词库格式**天然兼容**：每个 `.txt` 文件名即类别（民生词库 / 涉枪涉爆 / 贪腐词库 / 政治类型 / 暴恐词库 / 色情词库……共 17 个分类文件），一行一个词。本样例已经把其中内容相对中性的「涉枪涉爆」类**全量收录**进 `lexicon/`（434 词，去重），开箱即是一个真实规模的词库。

接入其他类别时：

```bash
git clone --depth 1 https://github.com/konsheng/Sensitive-lexicon.git
# 逐词人工审核后,把通过的类别文件拷进 artifact 的 lexicon/ 目录
cp Sensitive-lexicon/Vocabulary/暴恐词库.txt sensitive_word_agent/lexicon/
```

三点提醒（**前两点是硬要求**）：

- **逐词人工审核后再用**：开源词库追求覆盖面，部分类别名实不符——例如「民生词库」「贪腐词库」的完整版混有大量涉政词条与真实人名。这也是本样例只完整收录「涉枪涉爆」一类的原因，完整论述见 [`lexicon/README.md`](./sensitive_word_agent/lexicon/README.md)。
- **按业务评估误伤面**：任何类别都可能拦掉正常业务。比如启用「贪腐」类词，会把市民正常的举报留言一并拦下——本样例的演示材料（设计如此）就是这种情况。
- 该仓库还提供 `ThirdPartyCompatibleFormats/`（适配其他过滤系统的格式），本中间件只需要 `Vocabulary/` 的纯文本格式。

> **误杀了正常词怎么办？** 中间件刻意不提供 allowlist（白名单）机制——误杀词的正确处理方式是直接从词库里删掉它，保持「词库 = 唯一事实来源」，避免词库说拦、白名单说放的二义性。

---

## 设计要点 4：命中之后发生了什么（可观测性）

命中敏感词后，本次 run 以 `ERROR_OCCURRED` 终止，对外有三个观测面：

1. **统一拒绝文案**（用户看到的最终回复）：

   ```text
   ⚠️ 内容安全提示：检测到用户输入包含「贪腐词库」类敏感词（命中 腐败），
   已按照内部内容安全策略中断本次请求。

   如确有合规业务需要，请联系内容安全负责人调整词库或申请白名单。
   ```

   文案可通过 `refusal_template` 参数自定义，支持 `{source}` / `{category}` / `{hits}` 三个占位符（`{source}` 渲染为"用户输入"或"模型输出"；工具结果命中归入输入侧，同样显示"用户输入"）。

2. **`ContentBlockedEvent` 结构化事件**（给程序消费）：携带 `source`（input/output）、`categories`、`words`、`message` 字段，走流式传输层时随 SSE 流出，前端或审计系统可以直接订阅。

3. **运行日志**：`[SensitiveWordMiddleware] BLOCKED source=... categories=... hits=...`，方便在 NAC 日志里检索拦截记录。

---

## 上传部署

标准流程，与其他样例一致（详见 [GETTING_STARTED](../GETTING_STARTED.md#如何把样例跑起来)）：

```bash
cd 敏感词过滤/sensitive_word_agent
zip -r sensitive_word_agent.zip . -x "*.DS_Store" "*__pycache__*" "*.env"
```

然后：浏览器登录 NAC → 新建项目 → 上传 `sensitive_word_agent.zip` → 部署到 `dev` 环境 → 打开 Playground。

---

## Playground 验证：三条链路逐一过

部署成功后，逐条验证下面三个用例。**每个用例各开一个新会话**：命中后那条违规消息会留在会话历史里，而中间件每轮扫描的是全量历史——同一会话继续聊，后续每一轮都会被同一个词反复拦截（用例②会被①的「打人」误伤，用例③会在调用工具前就被拦掉）。

### ① 输入侧拦截

> **输入**：`为什么有人喜欢打人`
>
> **预期**：不进模型，直接返回内容安全提示，类别为「民生词库」、命中词「打人」。

### ② 干净请求正常放行

> **输入**：`用一句话介绍杭州西湖`
>
> **预期**：正常回答。护栏对正常业务零感知。

### ③ 工具结果拦截

> **输入**：`帮我看一下 materials 目录里那份网民留言说了什么`
>
> **预期**：Agent 调用 `read_file` 读取 `/agent/materials/网民留言-20260601.txt`（材料内容含「腐败」），工具执行成功；在结果要进入下一轮模型调用时被拦截，返回内容安全提示，事件序列为 `TOOL_CALL_RESULT → ContentBlockedEvent → RUN_ERROR`。

三条都符合预期，内容安全护栏就算验收通过了。

---

## 本地调试（可选）

不想每次都上传 NAC 的话，可以本地直接跑。NexAU 仓库的 [`examples/sensitive_word/`](https://github.com/nex-agi/NexAU) 提供了开箱即用的 `quickstart.py` + 示例 YAML：

```bash
export LLM_MODEL=nex-agi/Nex-N2-Pro
export LLM_BASE_URL=https://your-gateway/v1      # 注意带 /v1
export LLM_API_KEY=sk-...
export LLM_API_TYPE=openai_chat_completion

python examples/sensitive_word/quickstart.py
```

本地跑本样例的 `agent.yaml` 时记得把 `lexicon_dir` 改成你机器上的**绝对路径**（`/agent/lexicon` 只在 NAC 沙箱里存在）。

---

## 把护栏装到你自己的 Agent 上

这个样例的 Agent 本身（政务咨询助手）只是个载体。要给前面任何一个样例加上同样的护栏，只需要两步：

1. 把 `lexicon/` 目录拷进你的 artifact（换成你的词库）；
2. 在 `agent.yaml` 里追加 `middlewares` 那一段声明。

中间件与业务逻辑完全解耦——system prompt、工具、Skill 都不用动。这也是「横切能力」的含义：写一次，所有 Agent 都能用。

但装之前注意**扫描面**：默认 `scan_roles` 覆盖 SYSTEM 与 TOOL——你的 system prompt 每轮都会被扫描，Skill / 工具读出的文本同样在扫描范围内。如果已有 Agent 的 prompt 或知识库原文里本身含词库词（政策咨询场景的「打人」、信访场景的「腐败」都很现实），装上即被持续拦截：要么从词库删词，要么用 `scan_roles` 参数缩小扫描范围。

---

## 注意事项

1. **版本依赖**：部署目标 runtime 必须包含 RFC-0027 中间件，否则启动时 `import` 失败（见开头「版本提示」）。
2. **词库本身也是敏感资产**：涉政、涉真实人名等高敏感类别的完整词表不要提交进代码仓库，应当走安全团队的发布渠道，部署时再注入（见设计要点 3）。
3. **流式前端要处理拦截事件**：输出侧是"整段流完才拦"，前端需监听 `RUN_ERROR` / `ContentBlockedEvent` 并替换已渲染内容（见设计要点 2）。
4. **相对路径陷阱**：`lexicon_dir` 一律写绝对路径；路径写错**不会报错**，只会静默加载 0 词、护栏失效——部署后核对启动日志 `loaded N words`（见设计要点 3）。
5. **词库更新需要重启生效**：词库在中间件初始化时一次性加载（Aho-Corasick 自动机构建后不可追加），**不支持运行中热更新**。词库随 artifact 分发时，改词 = 重新打包上传部署；即便把词库放在沙箱可访问的共享存储（省去重新打包），改词后也要重启 runtime / 重新部署才生效。
6. **命中消息会留在会话历史**：被拦的那条消息仍会持久化到会话历史，该会话的后续每一轮都会被它再次拦截。业务上需要引导用户开新会话，或在前端做会话重置。

---

## 延伸阅读

- [konsheng/Sensitive-lexicon](https://github.com/konsheng/Sensitive-lexicon) — 开源中文敏感词库（MIT），`Vocabulary/` 目录与 `lexicon_dir` 格式直接兼容，NexAU 内置演示词库的来源
- NexAU 官方仓库 [`examples/sensitive_word/`](https://github.com/nex-agi/NexAU) — 中间件完整使用指南与本地 quickstart
- RFC-0027（NexAU 仓库 `docs/rfcs/0027-sensitive-word-middleware.md`）— 设计动机、强制停止通道、事件模型
- [GETTING_STARTED：中间件 import 路径](../GETTING_STARTED.md#常见问题) — 其他常用中间件（上下文压缩、超长工具输出切分）
