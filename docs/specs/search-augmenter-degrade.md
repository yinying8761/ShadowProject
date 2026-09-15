# 搜索路由降级：自动搜索 → 提示注入

> 日期：2026-09-14 | 状态：**已确认，待实施**（2026-09-14 讨论定稿）
> 来源：iteration-plan3 交付后实测发现的误触发问题（详见下文真实案例）

## 问题

`core/router.py` 的关键词路由（`need_search`）+`services/message_augmenter.py`
的自动搜索会在**关键词命中时直接替 Agent 执行搜索（老 `research()`）**，
LLM 没有决策机会。

真实案例（2026-09-14 实测）：

- 用户消息："**刚刚**测试一下网络异常的气泡显示，重连后气泡消失"
- 命中 HARD_TRIGGER"刚刚" → 自动跑了一次老 `research()`（见下）
- 搜索结果全是"刚刚"的词典解释（confidence=low）→ 纯浪费

> 注：augmenter 自动搜索实际调用的是**老 `tools.search_tools.research()`**
> （`_do_search` + 一次总结 LLM 调用），**不是 SearchAgent 子智能体**——
> SearchAgent（有 `fetch_url`、最多 2 轮工具循环）只挂在主 Agent 的
> `research` 工具路径上，augmenter 从未用过它。（已确认：`报错.txt` 里的
> `[Search] query=... | confidence=` 日志行只来自老 `research()`。）

**代价**：每次误触发 = 1 次搜索 + 1 次独立 LLM 总结调用（老 `research()`），
且结果注入主上下文继续膨胀 token。HARD_TRIGGERS 多为"今天/最新/最近/刚刚"
等日常高频词，日常闲聊误触发率高。

## 已做的临时缓解（iteration-plan3 之外，2026-09-14 已交付）

修剪 HARD_TRIGGERS：移除 13 个高频误触发词（今天/最新/最近/刚刚/发布/日期/
时间/定义/解释一下/参数/配置/性能/版本/在哪里），新增组合短语
（最新消息/刚刚发布）。行为探针 13/13 通过，全量测试 322/322 通过。

## 提案（本 spec 的核心）

**把 augmenter 从"替 Agent 执行搜索"降级为"给 Agent 的提示注入"**：

- 现在：`need_search()` 命中 → augmenter 直接执行搜索（老 `research()`）→ 结果注入上下文
- 提案：`need_search()` 命中 → 只在上下文注入一句提示（"用户消息可能涉及时效
  或外部信息，可考虑调用 research 工具核实"）→ **主 Agent 的 LLM 自主决定是否搜索**

判断链（确认过的架构理解）：

- **要不要搜** → 主 LLM 判断（看得懂语境）
- **怎么搜** → SearchAgent 内的小 LLM 判断（`search`/`fetch_url` 工具循环）
- `need_search()` 不再执行任何搜索，只决定"是否注入那句提示"

### 为什么这是更对的架构

1. **决策权归 LLM**：与项目设计哲学一致（"LLM 做决策、代码执行决策"）——
   主 Agent 本来就注册了 `research` 工具且用得好（eval T03 证明），关键词
   路由是在和 Agent 的自主决策抢活，还抢得又快又错
2. **零误触发成本**：误判只剩一句提示词，不再是真金白银的"1 次搜索 + 1 次
   独立 LLM 调用"
3. **LLM 判断比关键词准**：它看得懂语境（"今天好累" vs "今天天气"），关键词看不懂

### 代价（必须接受）

- 确实需要搜索的消息：多一轮工具调用往返（延迟 +1 轮 + 一次主 LLM 调用）
- 主动陪伴等无工具流程不受影响（augmenter 本就只挂 chat 链路）

### SOFT_TRIGGERS 的处理（已定）

产品/技术/游戏名（Python、iPhone、原神…）与 HARD_TRIGGERS 走同一条**提示注入
路径**：命中即在上下文注入那句提示，不执行搜索。提示注入把误触发成本压到
"一句提示"，词表维持现状即可，无需再为误触发率修剪。

## Considered Options

- **维持自动搜索 + 持续修剪关键词**（已做，作为过渡）：黑名单式打补丁永远
  修不完，且无法解决"关键词看不懂语境"的根本问题
- **彻底删除关键词路由**：更激进——完全交给 LLM（主 Agent 本就注册 `research`
  工具，eval T03 证明会用）。与本提案（提示注入）相比：删路由连"关键词
  nudge"也一起去掉，
  真需求代价两者相同（+1 轮往返），真正差别只在"该搜却漏搜"时 nudge 能救回
  多少；POI 注入是独立 Stage 2，删搜索路由不受影响。**回退路径**：先实施本
  提案（提示注入），若上线实测"该搜却漏搜"无显著回升，退化为删路由（纯 LLM
  + system prompt）
- **自动搜索 + LLM 二次确认**：每次命中再花一次 LLM 调用判断真假——成本
  接近直接搜，没意义

## 改动预估

| 文件 | 改动 |
|---|---|
| `services/message_augmenter.py` | Stage 1 从"执行搜索+注入结果"改为"注入提示语"；删除对 `cache_get/cache_set` 的依赖 |
| `core/router.py` | `need_search` 保留（HARD + SOFT 都进提示路径）；5 分钟结果缓存删除（提示注入不需要缓存结果） |
| `tools/search_tools.py` | **删除老 `research()`**（SearchAgent 已取代它；`_do_search`/`_search_*`/`fetch_url` 保留，SearchAgent 内部仍用） |
| 测试 | augmenter 行为变更的用例更新 + 提示注入断言；检查/清理针对老 `research()` 的用例 |

**约 0.5-1 天（含测试）。**

## Out of Scope

- POI 注入等其他 augmenter 阶段——不动
- proactive/greeting 链路——本就不经过 augmenter
- SOFT_TRIGGERS 的词表维护——沿用现状

## Future（本次不做，留档）

- **确定性时效信号**：时效类误触发的根因是"用关键词猜时效"。未来可考虑在
  上下文注入当前时间（应用本就掌握精确时间），让 LLM 自己判断"是否涉及时效/
  旧闻、要不要搜"，替代关键词猜测。与提示注入不冲突、可叠加。