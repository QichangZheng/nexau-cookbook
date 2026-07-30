# 教程：聚合搜索 — 一个工具接口，背后可换四家搜索服务商

> 🔍 横切能力 · 进阶 ｜ 约 45 分钟 ｜ 多服务商适配器 + 23 个环境变量 + 「配了不生效」的可观测性

前面的样例里，Agent 的信息来源要么是随 artifact 打包的知识库（Skill），要么是内网的数据库和 API。这一篇解决的是第三类来源：**公网实时信息**。

联网检索看起来是个小需求——调个搜索 API 就行。但真做起来会撞上三件事：**国内外服务商的能力和字段完全不一样**；**政务场景下"只要官方来源"这类需求，有的服务商原生支持、有的根本没有**；**部署环境变了（内网 / 信创 / 换供应商），不能让业务代码跟着改**。

本篇构建一个「政策情报助手」，用**一个工具接口**统一封装 4 家搜索服务商（Serper / 豆包搜索 / 百度 AI 搜索 / 北坡聚合搜索）。换服务商只改一个环境变量——不动代码、不重新打包、不改 system prompt。

---

## 目录

- [前置条件](#前置条件)
- [场景与目标](#场景与目标)
- [项目结构](#项目结构)
- [设计要点 1：Provider 与 Engine 是两层，别混成一个](#设计要点-1provider-与-engine-是两层别混成一个)
- [设计要点 2：能力不齐怎么办——降级、不报错、但要留痕](#设计要点-2能力不齐怎么办降级不报错但要留痕)
- [设计要点 3：参数三级优先级与 None 哨兵](#设计要点-3参数三级优先级与-none-哨兵)
- [设计要点 4：密钥走 Runtime 环境变量，不是沙箱环境变量](#设计要点-4密钥走-runtime-环境变量不是沙箱环境变量)
- [设计要点 5：检索预算——不写死会得到「零回复」](#设计要点-5检索预算不写死会得到零回复)
- [设计要点 6：错误分两类，只有一类值得重试](#设计要点-6错误分两类只有一类值得重试)
- [上传部署](#上传部署)
- [Playground 验证](#playground-验证)
- [换一家服务商：只改环境变量](#换一家服务商只改环境变量)
- [把聚合搜索装到你自己的 Agent 上](#把聚合搜索装到你自己的-agent-上)
- [注意事项](#注意事项)
- [延伸阅读](#延伸阅读)

---

## 前置条件

| 工具 / 资源 | 要求 | 验证 |
|---|---|---|
| NAC 账号 + 项目 | 已注册 [North Agent Cloud](https://nac.xiaobei.top) 并建了一个项目 | 浏览器登录 → 项目列表能看到 |
| 搜索服务商 API Key | **至少一家**即可，见下表 | 用下面的 curl 自测能拿到 200 |
| Python 依赖 | 只用 `httpx`（NAC runtime 自带） | 无需 `nexau.json` 的 `setup` |

四家服务商任选其一起步（**不需要全都申请**）：

| Provider | 适合 | 申请入口 | 免费额度 |
|---|---|---|---|
| `Serper` | 英文 / 技术内容，快（1~2 秒） | <https://serper.dev/api-keys> | 注册赠送额度 |
| `Seed`（豆包搜索） | 中文内容，最快（<1 秒），权威度分级 | [控制台](https://console.volcengine.com/) · [文档](https://docs.volcengine.com/docs/87772/2272953) | 每账号每月 500 次 |
| `Baidu`（百度 AI 搜索） | 中文内容，站点过滤好用 | [控制台](https://console.bce.baidu.com/ai-search/home) · [文档](https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5) | **每天 100 次** |
| `XiaoBei`（北坡聚合搜索） | 中英文全覆盖，可选底层引擎（google/bing/baidu） | [文档](https://search.xiaobei.top/docs)（申请 Key 请联系服务管理员） | 按 Key 限流 |

拿到 Key 后先自测（以 Serper 为例）：

```bash
curl -s -X POST https://google.serper.dev/search \
  -H "X-API-KEY: <你的KEY>" -H 'Content-Type: application/json' \
  -d '{"q":"test","num":2}' | head -c 200
```

---

## 场景与目标

政策研究岗有三类高频需求，正好把聚合搜索的三种能力问全：

1. 「国家层面关于人工智能有哪些**最新**政策」→ 需要**时效过滤**；
2. 「**只看政府官网**」→ 需要**站点/权威度过滤**；
3. 「arxiv 上最近一周的大模型论文」→ 需要**垂直类型检索**。

目标是构建一个政策情报助手：

1. 一个 `AggregatedWebSearch` 工具接口，后端可在 4 家服务商间切换；
2. 19 个检索参数全部开放给模型，且每个都能由部署方用环境变量设默认值；
3. 服务商不支持的参数**静默降级但留痕**，调用永远不会因此失败。

---

## 项目结构

```text
聚合搜索/
└── websearch_agent/                        ← 上传到 NAC 的完整 artifact
    ├── nexau.json
    ├── agent.yaml                          ← 注册工具；注意 max_iterations
    ├── systemprompt.md                     ← 检索预算规则写在最前面
    ├── custom_tools/
    │   ├── __init__.py
    │   └── aggregated_websearch.py         ← 核心：4 个 Provider 适配器 + 参数解析
    └── tools/
        └── AggregatedWebSearch.tool.yaml   ← 19 个参数的 schema + 部署配置注释块
```

| 文件 | 职责 |
|---|---|
| `custom_tools/aggregated_websearch.py` | 4 个 Provider 适配器、统一重试、参数归一。约 1500 行，其中一半是注释和上游契约说明 |
| `tools/AggregatedWebSearch.tool.yaml` | 模型看到的参数契约；**文件顶部有一大段 `#` 注释**，是给部署者看的环境变量速查 |
| `systemprompt.md` | 「检索预算」硬规则 + 「意图 → 参数」映射表 |

---

## 设计要点 1：Provider 与 Engine 是两层，别混成一个

第一版我把这两个概念合成了一个环境变量 `SEARCH_ENGINE`，很快就不够用了。它们其实是两层：

- **Provider（服务商）** —— 调**哪家**的搜索 API：`Serper` / `Seed` / `Baidu` / `XiaoBei`；
- **Engine（底层搜索引擎）** —— 结果实际**来自哪个引擎**：`google` / `bing` / `baidu`。

多数服务商的引擎是固定的（Serper 背后就是 Google，豆包用自有索引）。但**聚合型服务商**（这里是 XiaoBei）自己就并发打多个引擎，这时才需要选。所以是两个变量：

```bash
SEARCH_PROVIDER = XiaoBei        # 调哪家
SEARCH_ENGINE   = google|bing    # 让这家用哪些底层引擎（仅聚合型服务商有效）
```

代码里对应两层结构：`SearchProviderBase` 基类管重试、退避、错误归一、参数告警；四个子类各自只实现「发一次请求 + 把返回归一成统一结构」。

```python
class SearchProviderBase(ABC):
    name: str = "base"
    supports_engine_choice: bool = False      # 是否允许选底层引擎
    IGNORED_PARAMS: frozenset[str] = frozenset()   # 本家不支持、会被忽略的参数

    @abstractmethod
    def _do_search(self, client, query, options) -> list[dict]:
        """发一次请求，返回**已归一**的结果列表"""
```

归一后的每条结果长这样，四家一致——上层（system prompt、前端）不需要知道背后是谁：

```python
{"title": ..., "link": ..., "snippet": ..., "date": ...,
 "provider": "Baidu", "engine": None, "position": 1}
```

> 💡 **命名教训**：一开始四个适配器类都叫 `*Engine`（`SerperEngine`…），但它们表达的是 Provider。等真正引入 Engine 这一层时，`SearchEngineBase` 到底指哪层就说不清了。**概念分层没想清楚之前先别定名**，否则改名要动几十处。

---

## 设计要点 2：能力不齐怎么办——降级、不报错、但要留痕

四家服务商的能力差异很大。以「只要非常权威的来源」为例：豆包有原生的 `Filter.AuthInfoLevel`，Serper 完全没有这个概念。

处理原则是三条，**缺一不可**：

1. **不报错**：给 Serper 传 `authority_only=true`，调用照常成功，只是那个参数不起作用；
2. **能降级就降级**：Serper 没有站点过滤字段，但它把 query 透传给 Google，所以把 `sites` 编译成 `site:` 查询词算子；
3. **必须留痕**：静默忽略是最难排查的一类问题，所以每个被忽略的参数都打一条 warning。

第 3 条不是纸上谈兵。开发这个工具时，我给 Baidu 传了 `authority_only=true`，结果全是自媒体——因为 Baidu 不支持这个参数，而当时代码**静默忽略、没有任何提示**。现在会明确告诉你：

```text
Baidu 不支持 authority_only，本次传入的 True 已忽略
Baidu 不支持 industry，本次传入的 'gov' 已忽略
Serper 不支持选择底层搜索引擎，search_engine='baidu' 已忽略
```

实现上，每个 Provider 声明自己忽略哪些参数，基类统一比对并告警：

```python
class BaiduProvider(SearchProviderBase):
    IGNORED_PARAMS = frozenset({
        "authority_only", "industry", "query_rewrite", "need_content",
        "full_content", "content_format", "country", "language", "location", "page",
    })
```

降级的收益有多大？以 XiaoBei 上的站点过滤为例：**纯靠本地按 host 过滤，`arxiv.org` 只能捞到 1/28 条；把 `site:arxiv.org` 拼进查询词后是 20/20**，耗时还从 33 秒降到 6 秒。所以两者要**同时**做——算子在上游收敛，本地过滤兜底（个别下游引擎会忽略算子）。

完整的支持度矩阵写在 `aggregated_websearch.py` 的模块 docstring 里，节选：

| 参数 | Serper | Seed | Baidu | XiaoBei |
|---|:--:|:--:|:--:|:--:|
| `time_range` 预设 | ✅ `tbs=qdr:*` | ✅ 原生 | ✅ `search_recency_filter` | ✅ |
| `time_range` 自定义区间 | ✅ `tbs=cdr:*` | ✅ 原生 | ✅ `range.page_time` | ❌ |
| `sites` | ✅ 查询词算子 | ✅ `Filter.Sites` | ✅ `match.site` | ✅ 算子 + 本地 |
| `block_hosts` | ✅ 查询词算子 | ✅ 原生 | ⚠️ 上游无效，走本地过滤 | ✅ |
| `authority_only` | ❌ | ✅ `AuthInfoLevel` | ❌ | ❌ |
| `search_engine` | ❌ | ❌ | ❌ | ✅ `engines` |

---

## 设计要点 3：参数三级优先级与 None 哨兵

19 个参数，模型调用时可以传，部署方也需要能设「本部署的默认值」（比如政务部署想让 `industry` 恒为 `gov`）。

命名规则：**`SEARCH_` + 参数名大写**（参数名自带 `search_` 前缀的去重）：

```text
content_format → SEARCH_CONTENT_FORMAT
num_results    → SEARCH_NUM_RESULTS
search_engine  → SEARCH_ENGINE     ← 不是 SEARCH_SEARCH_ENGINE
```

优先级是 **调用方显式传参 > 环境变量 > 内置默认值**。

这里有个坑值得单独讲。最直觉的实现是「参数值等于默认值 = 没传」，但**这样会时灵时不灵**——实测模型会把默认值原样回传：

```json
{"query":"LLM","content_format":"text","max_content_chars":1000,"authority_only":false, ...}
```

模型其实并不在意 `content_format`，只是照着 schema 把默认值填了。如果按值判等，部署方设的 `SEARCH_CONTENT_FORMAT=markdown` 这次就被忽略，下次模型没填时又生效——非确定性行为，排查起来极痛苦。

所以函数签名的默认值**一律是 `None` 哨兵**，只有 `None` 才表示"没传"：

```python
def aggregated_websearch(
    query: str,
    num_results: int | None = None,        # ← 不是 = 10
    content_format: str | None = None,     # ← 不是 = "text"
    ...
):
    options = SearchOptions(**{
        name: resolve_param(name, value) for name, value in supplied.items()
    })
```

环境变量是字符串，所以 `resolve_param` 按内置默认值的类型做转换（整数、布尔）。**写错值不打挂检索**，回退默认并告警：

```text
环境变量 SEARCH_NUM_RESULTS='abc' 不是整数，已回退默认 10
```

> ⚠️ **一式两份必然漂移**：`.tool.yaml` 的 `default` 和 Python 侧的默认值是两处独立声明，改一处忘另一处，就会出现「模型以为默认是 10、实际是 5」。建议写个校验脚本，改完就跑：
>
> ```python
> spec = yaml.safe_load(open("tools/AggregatedWebSearch.tool.yaml"))["input_schema"]
> sig = inspect.signature(aggregated_websearch)
> assert set(spec["properties"]) == set(sig.parameters)          # 参数集合一致
> assert all(spec["properties"][n].get("default") == PARAM_DEFAULTS.get(n)
>            for n in spec["properties"] if n != "query")        # 默认值对齐
> ```

---

## 设计要点 4：密钥走 Runtime 环境变量，不是沙箱环境变量

这一条和 [GETTING_STARTED 的环境变量配置](../GETTING_STARTED.md#环境变量配置) 有区别，**配错了工具会一直报「缺 API Key」**。

NAC 的环境变量有两个 scope：

- **Runtime scope** → 注入 agent-runtime 进程（`custom_tools` 里的 Python 代码就跑在这里）；
- **Sandbox scope** → 注入 gVisor 沙箱容器（`run_shell_command` 那类工具执行的地方）。

`custom_tools` 用 `os.getenv()` 读到的是 **Runtime scope**。实测对照（同一个 lane，两个 scope 各配了变量，在 Runtime 进程里 `env | grep`）：

```text
Runtime scope 配的：SEARCH_API_KEY / SEARCH_PROVIDER / TEST111   → ✅ 全部可见
Sandbox scope 配的：HOST_PROXY / TEST222                          → ❌ 完全不可见
```

所以在 NAC 控制台配置时，要选 **Runtime**（运行时）那一栏，不是「沙箱环境」。

顺带说说密钥为什么不进 `input_schema`：模型只要看得见就可能把它写进 tool_call JSON → 对话历史 → 日志，也可能自己编一个假 Key。本工具的密钥**从来不经过模型**——它由模块内部直接 `os.getenv("SEARCH_API_KEY")` 读取。

> 💡 这与 [金融数据智能体](../金融数据智能体/) 用的 `extra_kwargs: ${env.XXX}` 是两种做法，目的一样（都不让模型看到密钥）。区别在于：`extra_kwargs` 适合「每次调用都要带、且数量少」的参数；本工具是 **23 个配置项**（5 个服务商级 + 18 个参数级），逐个走 `extra_kwargs` 会让 `agent.yaml` 膨胀到没法看，所以改成模块内部读环境变量。

---

## 设计要点 5：检索预算——不写死会得到「零回复」

这是本篇最值得抄走的一条，也是我实际踩到的坑。

最初的 system prompt 只写了「每条结论都要给出处」，没有限制检索次数。结果在 `max_iterations: 20` 下，两个测试问题分别触发了 **19 次和 17 次**检索——模型陷入「再换个关键词搜一次说不定更全」的循环。

把 `max_iterations` 降到 10 之后，出现了更糟的情况：

```text
status=completed   ← 平台认为正常结束
content = "\n\n"   ← 但一个字的回答都没有
消息序列：9 条 assistant 消息，全部是 tool_calls，零最终答案
```

**撞上 `max_iterations` 时，`status` 仍然是 `completed`，但 `content` 是空的**——典型的静默失败，前端会显示成"Agent 没说话"。

根因是我的约束写得太软（「大多数问题 1~2 次就够，绝不要超过 4 次」这种表述模型不当回事）。改成**带编号的硬停止规则**，并放在 system prompt 最前面：

```markdown
## 🚦 最高优先级规则：检索预算

**你每轮对话最多调用 `AggregatedWebSearch` 3 次。**

- 第 1 次检索后，先判断已有结果能不能回答问题。能，就立刻写答案，不要再搜。
- 第 3 次检索完成后，无论结果是否理想，都必须停止检索并直接作答。
- 达到上限时不要沉默、不要继续调工具——基于已拿到的结果作答。

违反这条规则的后果是：对话会因为达到迭代上限而**没有任何回复返回给用户**。
```

效果（同样的问题，同一个模型）：

| 问题 | 改前 | 改后 |
|---|---|---|
| 「只看政府官网，查人工智能政策」 | 17 次检索 → **空回答** | **1 次** → 1192 字带出处 |
| 「国家层面有哪些最新政策」 | 19 次检索 | **4 次** → 1867 字 |
| 「arxiv 最近一周的大模型论文」 | — | **2 次** → 1348 字 |

两条通用经验：

- **`max_iterations` 是安全网，不是控制手段**。真正的约束要写进 prompt，而且要给**具体数字**和**违反后果**；
- 任何会被反复调用的工具（搜索、爬取、查库）都要在 prompt 里立预算，否则模型天然倾向于"再来一次"。

---

## 设计要点 6：错误分两类，只有一类值得重试

工具**永不抛异常**，失败一律返回结构化 error（这是 NexAU 内置工具的既有约定）：

```python
{"content": "Error: ...", "returnDisplay": "Error performing web search.",
 "error": {"message": "...", "type": "WEB_SEARCH_CONFIG_ERROR"}}
```

两种 type 的区别对使用者很重要：

| type | 含义 | 该怎么办 |
|---|---|---|
| `WEB_SEARCH_CONFIG_ERROR` | 缺 Key、Provider 名写错、账号额度用尽、套餐不支持 | **重试无用**，去改配置 |
| `WEB_SEARCH_FAILED` | 上游超时、5xx、限流 | 可稍后重试（工具内部已做 3 次指数退避） |

分类不能拍脑袋，要照上游文档来。豆包搜索有个**必须照做的坑**：

> **它失败时也返回 HTTP 200**，错误藏在 `ResponseMetadata.Error` 里。只看状态码会把失败当成功。

而且它的错误码要分开对待——文档标注 `10500`（服务端内部错误）和 `700429`（QPS 限流）"一般可重试解决"，其余（额度用尽、权限、参数错误）重试无益：

```python
RETRYABLE_ERROR_CODES = {"10500", "700429"}

error = (data.get("ResponseMetadata") or {}).get("Error")
if error:
    code = str(error.get("Code") or error.get("CodeN"))
    if {code, str(error.get("CodeN"))} & self.RETRYABLE_ERROR_CODES:
        raise RetryableUpstreamError(detail)   # 退避后重试
    raise SearchProviderError(detail)          # 直接上抛，不浪费时间
```

还有一种情况**不是错误**：条件收窄过头导致 0 条结果时，返回的是**成功且 `sources: []`**（content 写 "No results found."）。这让模型知道"搜过了但确实没有"，而不是当成故障去重试或编造答案。

---

## 上传部署

标准流程，与其他样例一致（详见 [GETTING_STARTED](../GETTING_STARTED.md#如何把样例跑起来)）：

```bash
cd 聚合搜索/websearch_agent
zip -r websearch_agent.zip . -x "*.DS_Store" "*__pycache__*" "*.env"
```

浏览器登录 NAC → 新建项目 → 上传 `websearch_agent.zip` → 部署到 `dev` 环境。

**部署后配置环境变量**（控制台 → 环境变量 → **Runtime** scope），最少两个：

```bash
SEARCH_PROVIDER = Seed                    # 或 Serper / Baidu / XiaoBei
SEARCH_API_KEY  = <对应服务商的 Key>
```

> ⚠️ `SEARCH_MAX_RETRIES` 的语义是**总尝试次数（含首次）**，不是"额外重试几次"。
> 工具内部已钳到下限 1，配成 `0` 不会变成"一次都不试"。

改完环境变量需要 **Redeploy** 才生效。

可选的部署级默认值（举两个实用的）：

```bash
# 政务部署：默认只要官方权威来源
SEARCH_AUTHORITY_ONLY = true
SEARCH_INDUSTRY       = gov

# 控制上下文预算：条数 × 单条长度
SEARCH_NUM_RESULTS       = 5
SEARCH_MAX_CONTENT_CHARS = 800
```

完整的 23 个变量速查表在 `tools/AggregatedWebSearch.tool.yaml` **文件顶部的注释块**里。那段注释会被 YAML 解析器丢弃、**不进模型上下文**（实测：文件 11488 字节，进模型的 description 只有 2111 字节），所以可以写得很详细而不浪费 token。

---

## Playground 验证

部署成功后逐条验证。下面的「实测」是本样例在 NAC beta 上的真实运行结果。

### ① 时效检索

> **输入**：`国家层面关于人工智能有哪些最新政策？`
>
> **预期**：模型自己带上 `time_range` 和 `industry="gov"`，返回带发布时间和链接的政策列表。
>
> **实测**：4 次检索，1867 字回答，命中《国务院关于深入实施"人工智能+"行动的意见》等政策原文。

### ② 站点限定

> **输入**：`只看政府官网，查一下人工智能相关政策`
>
> **预期**：模型传 `sites` 或 `industry="gov"`，结果全部来自 `*.gov.cn`。
>
> **实测**：**1 次**检索，1192 字回答，来源全部为政府官网。

### ③ 垂直类型检索

> **输入**：`arxiv 上最近一周有哪些关于大语言模型的论文？`
>
> **预期**：模型传 `sites="arxiv.org"` + `time_range="OneWeek"`（或 `search_type="scholar"`）。
>
> **实测**：2 次检索，1348 字回答，并主动说明"arXiv 是预印本平台，论文尚未经同行评议"。

### ④ 参数不支持时不报错

> **输入**：`只要非常权威的官方来源，查人工智能政策`（在 `SEARCH_PROVIDER=Serper` 下）
>
> **预期**：调用**成功**（Serper 不支持 `authority_only`，静默降级），运行日志里有
> `Serper 不支持 authority_only，本次传入的 True 已忽略`。

四条都符合预期，聚合搜索就算验收通过了。

---

## 换一家服务商：只改环境变量

这是本篇的核心价值，值得单独演示一次。

本样例开发过程中，Baidu 的每日 100 次免费额度被测试用尽了：

```json
{"code": "QUOTA_USER_DAILY_FREE", "message": "Daily free quota per user for Web Search exceeded"}
```

Agent 的表现是**如实报告、没有编造**（system prompt 第 5 条的效果）：

> 我尝试按"只看政府官网/政务权威源"检索"人工智能相关政策"，但搜索工具两次返回配额错误，无法完成实时核验……

切换到 Serper 只需要改两个环境变量并 Redeploy：

```bash
SEARCH_PROVIDER = Serper
SEARCH_API_KEY  = <Serper 的 Key>
```

**代码不动、artifact 不重新打包、system prompt 不改。** 下一次对话里工具返回的 `provider` 字段就变成了 `Serper`，结果照常返回。

这个能力在几种场景下很实在：

- 某家服务商挂了 / 限流 / 欠费 → 立刻切换，不用等发版；
- 中文场景用豆包、英文场景用 Serper → 两套部署共用同一份代码；
- 信创 / 内网环境不能连公网服务商 → 用 `SEARCH_BASE_URL` 指到内网代理，或加一个内网 Provider 适配器。

---

## 把聚合搜索装到你自己的 Agent 上

三步：

1. 把 `custom_tools/aggregated_websearch.py` 和 `tools/AggregatedWebSearch.tool.yaml` 拷进你的 artifact；
2. 在 `agent.yaml` 的 `tools:` 里加三行注册；
3. 配 `SEARCH_PROVIDER` + `SEARCH_API_KEY` 两个 Runtime 环境变量。

```yaml
tools:
  - name: AggregatedWebSearch
    yaml_path: tools/AggregatedWebSearch.tool.yaml
    binding: custom_tools.aggregated_websearch:aggregated_websearch
```

**别忘了在 system prompt 里立检索预算**（设计要点 5），否则大概率会遇到那个「零回复」。

### 加一家新的服务商

继承基类，实现一个方法即可，重试/退避/告警/参数解析都由基类接管：

```python
class MyProvider(SearchProviderBase):
    name = "MyProvider"
    default_base_url = "https://search.internal.example.com"
    IGNORED_PARAMS = frozenset({"authority_only", "industry"})   # 本家不支持的

    def _do_search(self, client, query, options):
        resp = client.post(f"{self.base_url}/search",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"q": query, "n": options.num_results})
        resp.raise_for_status()
        return [{"title": x["t"], "link": x["u"], "snippet": x["s"],
                 "date": None, "source": None} for x in resp.json()["items"]]

_PROVIDER_REGISTRY["myprovider"] = MyProvider
```

---

## 注意事项

1. **环境变量选 Runtime scope**，不是沙箱环境。配错了工具会一直报「缺 API Key」（设计要点 4）。
2. **改环境变量后要 Redeploy** 才生效。lane 级环境变量只支持**批量 PUT 替换**（没有单条 POST，试了会返回 405），所以改一个变量必须把其余的一起传，漏了会被清掉。
3. **免费额度差异很大**：百度每天 100 次、豆包每月 500 次。开发调试很容易用光——本篇写作过程中就用光了百度的当日额度。生产部署前确认套餐。
4. **必须在 prompt 里立检索预算**，否则会遇到 `status=completed` 但 `content` 为空的静默失败（设计要点 5）。
5. **上下文预算 = `num_results` × `max_content_chars`**。默认 10 × 1000 = 单次最多 1 万字符进上下文；开 `full_content` 后单条可能上千字，务必同步调小条数。
6. **`.tool.yaml` 的 `default` 与 Python 默认值要对账**，一式两份必然漂移（设计要点 3）。
7. **服务商的坑要写进注释**：豆包失败返 HTTP 200、`Snippet` 官方不建议喂大模型（应该用 `Summary`）、Serper 单独指定 `google` 时会静默回落到其他引擎、百度 `block_websites` 上游无效——这些都不在直觉里，代码注释里都标了出处。

---

## 延伸阅读

- [金融数据智能体](../金融数据智能体/) — 单个 REST API 的封装范式，以及 `extra_kwargs` 注入密钥的做法（与本篇的取舍对比见设计要点 4）
- [敏感词过滤](../敏感词过滤/) — 另一个「横切能力」样例：写一次，所有 Agent 都能用
- [GETTING_STARTED：环境变量配置](../GETTING_STARTED.md#环境变量配置) — NAC 环境变量的通用说明（注意本篇设计要点 4 指出的 scope 区别）
- 各服务商官方文档：[Serper](https://serper.dev/playground) · [豆包搜索](https://docs.volcengine.com/docs/87772/2272953) · [百度 AI 搜索](https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5) · [北坡聚合搜索](https://search.xiaobei.top/docs)

---

> 📌 **后续更新**：本工具后续会用于覆盖 NexAU 内置的 `WebSearch` 工具。届时本教程会同步更新为「如何配置内置聚合搜索」，当前的自定义工具写法仍可作为「如何自己接一家服务商」的参考。
