"""
Hand-annotated eval cases for Agent tool-calling accuracy.

39 cases across 10 categories:

- tool_selection (4) / argument_accuracy (3) / answer_quality (3) / edge_case (3) /
  memory (2): the original single-turn set.
- multi_turn (6, ticket 07): pronoun reference, correction, cross-turn tool chains.
- recovery (7, ticket 07): tool error / timeout / empty result / circuit open /
  approval denied — the assertion is "degrades honestly", never "must succeed".
- group (6, ticket 08): ADR-0009 invariants, driven by a scripted model against the
  real scheduler (zero tokens, no network).
- vision (2, ticket 09) / search (3, ticket 09): **live** — skipped unless `--live`
  (or `EVAL_LIVE=1`); vision uses a generated fixture image, search must cite sources.
- T03 (tool_selection) is also **live** (ticket #76): `research` reaches the internet,
  and ADR-0012 boundary rule #1 forbids a non-live case from touching the network.

Cases are **data only**: multi-turn is `turns`, fault injection is `fail_tools`,
approval refusal is `deny_tools`, an attached image is `image_fixture`; adding a case
never touches the runner.
"""

from dataclasses import dataclass, field
from typing import Literal

#: 用例形态：单轮 / 多轮 / 失败恢复 / 群聊不变量（脚本化假模型）
EvalKind = Literal["single_turn", "multi_turn", "recovery", "invariant"]


@dataclass
class EvalCase:
    """A single hand-annotated test case for Agent evaluation."""

    id: str                                     # unique identifier, e.g. "T01"
    query: str = ""                             # single_turn 的用户输入
    description: str = ""                       # what this case tests
    kind: EvalKind = "single_turn"
    turns: list[str] = field(default_factory=list)  # multi_turn: 依次发送的用户消息
    #: **必备**工具集合（ticket #75）：必须包含于实际调用（顺序无关、允许重复调用）。
    #: `[]` = 必须不调用任何工具；`None` = 这条用例不关心工具（只断言回答）。
    expected_tools: list[str] | None = field(default_factory=list)
    ordered: bool = False                       # 单轮：期望工具**按顺序**精确出现（旧契约，仅测试用）
    forbidden_tools: list[str] = field(default_factory=list)  # 这些工具一个都不许出现
    #: 每工具**总调用次数**上界（ticket #75）：工具名 → 最多几次；空 = 不设上界。
    #: 口径是"总次数"，不是"最长连续段"—— 中间夹别的工具也照样累计（见 scoring.over_tool_caps）。
    max_tool_calls: dict[str, int] = field(default_factory=dict)
    expected_args_contain: dict[str, list[str]] = field(default_factory=dict)  # tool_name → substrings in args JSON
    answer_should_contain: list[str] = field(default_factory=list)    # keywords that MUST ALL appear (AND)
    answer_should_contain_any: list[str] = field(default_factory=list)  # keywords where ANY must appear (OR); empty = always true
    answer_should_not_contain: list[str] = field(default_factory=list)  # keywords that MUST NOT appear (NOT)
    #: 回答断言看哪一轮：多轮用例默认只看**最后一轮**的回复（"第二轮引用第一轮"的判定点）
    answer_scope: Literal["final", "all"] = "final"
    #: 可判定性质 2：相关工具被尝试且失败/被拒 + 回答非空（见 scoring.reported_not_completed）
    must_report_not_completed: bool = False
    #: 可判定检查都过、但还剩一项只有语义判断能定 → needs_review（报告单列，不进通过率分母）
    needs_review: bool = False
    #: needs_review 的原因（写进报告，给读的人/评分模型看）
    needs_review_reason: str = ""
    must_cite: bool = False                     # 回答里必须出现来源 URL（search 类）
    #: `must_cite` 时要求的**可信来源集合**（域名后缀，含子域）——只要求"有个 URL"
    #: 太弱，编一个也能过；"哪些域名算可信"由用例数据显式声明，评分器只做硬事实比较
    must_cite_hosts: list[str] = field(default_factory=list)
    #: invariant: 注册在 eval.group_turn_eval.INVARIANTS 里的名字
    invariants: list[str] = field(default_factory=list)
    #: invariant: 场景名（`eval.group_turn_eval.SCENARIOS` 里的键）
    scenario: str = ""
    max_model_calls: int | None = None          # 这条用例允许多少次模型调用
    #: recovery: 给这些工具注入故障（名字 → error / timeout / empty / circuit_open）
    fail_tools: dict[str, str] = field(default_factory=dict)
    #: recovery: 这些工具的审批会被拒绝（用户点了"拒绝"）
    deny_tools: list[str] = field(default_factory=list)
    #: vision: 这一轮要附上的**固定素材**图（`eval.fixtures.FIXTURES` 里的名字）
    image_fixture: str = ""
    live: bool = False                          # 需要真外网/真并发，默认不跑
    category: str = "tool_selection"            # tool_selection | argument_accuracy | answer_quality | edge_case | memory | …

    @property
    def user_turns(self) -> list[str]:
        """这次用例要依次发送的用户消息。

        单轮用例**只发一条**（spec: single_turn 取 turns[0]）：优先 `query`，
        没写 query 时退到 `turns[:1]`；多轮用例按 `turns` 逐条发。
        """
        if self.kind == "single_turn":
            if self.query:
                return [self.query]
            return list(self.turns[:1])
        if self.turns:
            return list(self.turns)
        return [self.query] if self.query else []


EVAL_CASES: list[EvalCase] = [
    # ═══════════════════════════════════════════════════════════════════
    # Category: tool_selection (4 cases)
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="T01",
        query="现在是什么时间？几点了？",
        description="时间查询 → 应调用 get_current_time，不应调用 research",
        expected_tools=["get_current_time"],
        # ticket #75：集合语义变宽后，"不该出现"的工具改由禁止集合显式声明
        forbidden_tools=["research"],
        answer_should_contain=[],
        answer_should_not_contain=[],
        category="tool_selection",
    ),

    EvalCase(
        id="T02",
        query="帮我读一下 F:/ShadowProject/README.md 这个文件的内容",
        description="读文件 → 应调用 read_file",
        expected_tools=["read_file"],
        expected_args_contain={"read_file": ["README.md"]},
        category="tool_selection",
    ),

    EvalCase(
        id="T03",
        query="帮我搜索一下 Python 最新版本有什么新特性",
        description="网页搜索 → 应调用 research（research 会真联网，所以标 live；默认运行不选它）",
        expected_tools=["research"],
        expected_args_contain={"research": ["Python"]},
        # ticket #76：research 会真的访问外网；标 live=False 却联网违反 ADR-0012 边界规则 #1。
        # 断言不变，只把"要不要跑"交给 --live 开关。
        live=True,
        category="tool_selection",
    ),

    EvalCase(
        id="T04",
        query="帮我抓取 https://httpbin.org/get 这个网页的内容",
        description="网页抓取 → 应调用 fetch_url",
        expected_tools=["fetch_url"],
        expected_args_contain={"fetch_url": ["httpbin.org"]},
        category="tool_selection",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: argument_accuracy (3 cases)
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="T05",
        query="列出 F:/ShadowProject 目录下的所有文件和文件夹",
        description="列目录 → path 参数应包含 ShadowProject",
        expected_tools=["list_directory"],
        expected_args_contain={"list_directory": ["ShadowProject"]},
        category="argument_accuracy",
    ),

    EvalCase(
        id="T06",
        query="在 F:/ShadowProject 目录下搜索所有 .tsx 后缀的文件",
        description="搜索文件 → root_path 应包含 ShadowProject，pattern 应包含 .tsx",
        expected_tools=["search_files"],
        expected_args_contain={
            "search_files": ["ShadowProject", ".tsx"],
        },
        category="argument_accuracy",
    ),

    EvalCase(
        id="T07",
        query="写一个文件到 F:/eval_test_output.txt，内容写 'Hello from eval test'",
        description="写文件 → path 和 content 参数正确",
        expected_tools=["write_file"],
        expected_args_contain={
            "write_file": ["eval_test_output.txt", "Hello from eval test"],
        },
        category="argument_accuracy",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: answer_quality (3 cases)
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="T08",
        query="1 + 1 等于几？",
        description="简单数学 → 回答应包含 2，不应出错",
        expected_tools=[],  # should not need any tool
        answer_should_contain=["2"],
        answer_should_not_contain=["错误", "error", "不知道"],
        category="answer_quality",
    ),

    EvalCase(
        id="T09",
        query="你好，请介绍一下你自己，你是谁？",
        description="自我介绍 → 回答是否说明了身份/角色（措辞属于语义判断，交人工/评分模型）",
        expected_tools=[],  # self-intro shouldn't need tools
        # ticket #76：没有可判定性质可断言（无工具、无失败、无来源、无上界）——
        # "有没有介绍清楚自己"只有语义判断能定，所以不列同义词，记 needs_review。
        needs_review=True,
        needs_review_reason="自我介绍是否说明了身份/角色属于语义判断（评分模型/人工），没有可判定性质可断言，不列同义词",
        category="answer_quality",
    ),

    EvalCase(
        id="T10",
        query="请用中文回答我：水是由什么元素组成的？",
        description="常识问题 → 直接回答含「氢」和「氧」，不需要搜索",
        expected_tools=[],  # common knowledge, no tool needed
        answer_should_contain=["氢", "氧"],
        answer_should_not_contain=["error", "错误"],
        category="answer_quality",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: edge_case (3 cases)
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="T11",
        query="你好！",
        description="简单问候 → 不应调用任何工具，直接友好回复",
        expected_tools=[],  # MUST be empty
        answer_should_contain=[],  # any friendly reply is fine
        answer_should_not_contain=["error", "错误"],
        category="edge_case",
    ),

    EvalCase(
        id="T12",
        query="先看看 F:/ShadowProject 目录下有什么文件，然后把 README.md 读给我听",
        description="多工具链 → 应先 list_directory 再 read_file",
        expected_tools=["list_directory", "read_file"],
        expected_args_contain={
            "list_directory": ["ShadowProject"],
            "read_file": ["README.md"],
        },
        category="edge_case",
    ),

    EvalCase(
        id="T13",
        query="帮我看看 F 盘下面有哪些文件夹",
        description="模糊查询但意图明确 → 应调用 list_directory 且路径包含 F:",
        expected_tools=["list_directory"],
        expected_args_contain={"list_directory": ["F:"]},
        category="edge_case",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: memory (2 cases)
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="T14",
        query="帮我记住：我最喜欢的编程语言是 Rust，别忘了哦",
        description="保存记忆 → 应调用 save_memory，content 包含 Rust",
        expected_tools=["save_memory"],
        expected_args_contain={"save_memory": ["Rust"]},
        category="memory",
    ),

    EvalCase(
        id="T15",
        query="我之前说过我喜欢什么编程语言？你记得吗？",
        description="检索记忆 → 应调用 search_memory",
        expected_tools=["search_memory"],
        category="memory",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: multi_turn (6 cases, ticket 07)
    # 多轮用例的 `expected_tools` 覆盖**所有轮次**；回答断言只看**最后一轮**
    # 的回复 —— 那正是"第二轮有没有正确引用第一轮"的判定点。
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="M01",
        kind="multi_turn",
        turns=["我叫小影，记住了吗？", "我叫什么名字？"],
        description="多轮指代：第二轮应从对话上下文里取出第一轮的名字（不判工具：可能会存记忆）",
        expected_tools=None,
        answer_should_contain=["小影"],
        category="multi_turn",
    ),

    EvalCase(
        id="M02",
        kind="multi_turn",
        turns=[
            "读一下 F:/ShadowProject/README.md",
            "不是这个，读 F:/ShadowProject/CONTEXT.md 开头",
        ],
        description="多轮纠正：用户否掉第一个文件后，应改读第二个（而不是重复第一次调用）",
        expected_tools=["read_file"],
        expected_args_contain={"read_file": ["CONTEXT.md"]},
        answer_should_not_contain=["找不到文件"],
        category="multi_turn",
    ),

    EvalCase(
        id="M03",
        kind="multi_turn",
        turns=[
            "列出 F:/ShadowProject 目录下有哪些文件",
            "把刚才看到的第一个 .md 文件读出来",
        ],
        description="跨轮工具链：第二轮要用第一轮的列目录结果去决定读哪个文件",
        expected_tools=["list_directory", "read_file"],
        expected_args_contain={"read_file": [".md"]},
        category="multi_turn",
    ),

    EvalCase(
        id="M04",
        kind="multi_turn",
        turns=["1+1 等于几？", "换个话题：2×3 等于几？"],
        description="话题切换 + 指代延续：第二轮答 6，且不该被上一轮的算术带偏",
        expected_tools=[],
        answer_should_contain=["6"],
        category="multi_turn",
    ),

    EvalCase(
        id="M05",
        kind="multi_turn",
        turns=[
            "记住：我最喜欢的编程语言是 Rust",
            "我最喜欢什么编程语言？",
        ],
        description="写入后追问：第二轮应直接引用上下文里的偏好（不判工具：记忆检索在评测里是桩）",
        expected_tools=None,
        answer_should_contain=["Rust"],
        category="multi_turn",
    ),

    EvalCase(
        id="M06",
        kind="multi_turn",
        turns=[
            "读一下 F:/ShadowProject/README.md",
            "我要的是 README.en.md，重新读一遍",
        ],
        description="多轮纠正（同一工具重调）：最后一次 read_file 的参数应换成 README.en.md",
        expected_tools=["read_file"],
        expected_args_contain={"read_file": ["README.en.md"]},
        category="multi_turn",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: recovery (7 cases, ticket 07)
    # 五种失败来源：工具报错 / 超时 / 空结果 / 熔断打开 / 审批拒绝。
    # 断言一律是"如实告知、不编造"，不是"必须成功"。
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="R01",
        kind="recovery",
        query="读一下 F:/ShadowProject/README.md 的内容",
        description="工具报错 → 应如实说读不到，而不是编造文件内容（工具被尝试且失败；措辞交给人工/评分模型）",
        expected_tools=["read_file"],
        # ticket #75：读文件用例不该动写工具；重试上界 = 账本阈值 2（不是"连续段"）
        forbidden_tools=["write_file"],
        max_tool_calls={"read_file": 2},
        fail_tools={"read_file": "error"},
        # ticket #76：可判定性质 2（工具被尝试且失败 + 回答了）取代关键词闭集；
        # "有没有编造内容"只有语义判断能定 → needs_review。
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否如实说明「读不到」、没有编造文件内容需要语义判断（评分模型/人工），不列同义词",
        max_model_calls=3,
        category="recovery",
    ),

    EvalCase(
        id="R02",
        kind="recovery",
        query="把 'hello' 写到 F:/eval_recovery_test.txt",
        description="审批拒绝 → 应说明没有执行（可判定性质 2：工具被拒 + 回答了；措辞交给人工/评分模型）",
        expected_tools=["write_file"],
        deny_tools=["write_file"],
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否说清了「没有写入」需要语义判断（评分模型/人工），不列同义词",
        max_model_calls=2,
        category="recovery",
    ),

    EvalCase(
        id="R03",
        kind="recovery",
        query="帮我搜一下今天的科技新闻",
        description="熔断打开 → 应说明暂时不可用（工具被尝试且失败；措辞交给人工/评分模型），且不编造来源链接",
        expected_tools=["research"],
        # ticket #75：research 连续失败 2 次后停用；允许换 1 次替代工具（fetch_url）
        forbidden_tools=["write_file"],
        max_tool_calls={"research": 2, "fetch_url": 1},
        fail_tools={"research": "circuit_open", "fetch_url": "circuit_open"},
        # ticket #76：性质 2 取代关键词闭集；"不可用/编没编新闻"的措辞交 needs_review。
        # 无来源 URL 仍可判定（性质 1：有没有来源），保留为硬事实检查。
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否说清了「搜索暂时不可用」、没有编造新闻需要语义判断（评分模型/人工），不列同义词",
        answer_should_not_contain=["http://", "https://"],
        category="recovery",
    ),

    EvalCase(
        id="R04",
        kind="recovery",
        query="搜索一下 Python 3.14 有什么新特性",
        description="搜索全失败 → 应说明没搜到（工具被尝试且失败；措辞交给人工/评分模型），不编造结论与来源",
        expected_tools=["research"],
        # ticket #75：research 连续失败 2 次后停用；允许换 1 次替代工具（fetch_url）
        forbidden_tools=["write_file"],
        max_tool_calls={"research": 2, "fetch_url": 1},
        fail_tools={"research": "error", "fetch_url": "error"},
        # ticket #76：性质 2 取代关键词闭集；"没搜到/编没编结论"的措辞交 needs_review。
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否说清了「没搜到/搜索失败」、没有编造结论需要语义判断（评分模型/人工），不列同义词",
        answer_should_not_contain=["http://", "https://"],
        category="recovery",
    ),

    EvalCase(
        id="R05",
        kind="recovery",
        query="看看我的屏幕上现在是什么",
        description="视觉不可用 → 应说明没看成（可判定性质 2：工具失败 + 回答了；措辞交给人工/评分模型）",
        expected_tools=["see_screen"],
        fail_tools={"see_screen": "error"},
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否说清了「没看到画面」需要语义判断（评分模型/人工），不列同义词",
        category="recovery",
    ),

    EvalCase(
        id="R06",
        kind="recovery",
        query="读 F:/ShadowProject/README.md 并总结要点",
        description="工具成功但返回空 → 应承认没有内容（空结果也是 trace 上可判定的「未完成」），而不是凭空总结",
        expected_tools=["read_file"],
        # ticket #75：读文件用例不该动写工具；重试/换文件读取最多 2 次
        forbidden_tools=["write_file"],
        max_tool_calls={"read_file": 2},
        fail_tools={"read_file": "empty"},
        # ticket #76：空结果 = 可判定的"未完成"证据（不靠关键词）；"有没有凭空总结"
        # 的措辞交 needs_review。
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否如实说明「没有内容/读不到」、没有凭空总结需要语义判断（评分模型/人工），不列同义词",
        category="recovery",
    ),

    EvalCase(
        id="R07",
        kind="recovery",
        query="抓取 https://example.com 上的内容",
        description="工具超时 → 应说明超时/失败（工具被尝试且失败；措辞交给人工/评分模型），不该假装抓到了",
        expected_tools=["fetch_url"],
        fail_tools={"fetch_url": "timeout"},
        # ticket #76：性质 2 取代关键词闭集；"超时/失败"怎么表述交 needs_review。
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否如实说明「超时/抓取失败」、没有假装抓到内容需要语义判断（评分模型/人工），不列同义词",
        category="recovery",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: group (6 cases, ticket 08) —— 群聊不变量
    # 脚本化假模型 + 真实调度器：零 token、零网络、秒级。
    # 期望不是"说了什么"，而是 ADR-0009 的上界与顺序。
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="G01",
        kind="invariant",
        scenario="three_all_speak",
        turns=["你们都在吗？"],
        description="3 人全员开口：全员起跑、每轮 ≤K、全局 ≤M、序号单调、沉默不落库、在途不取消",
        expected_tools=None,
        invariants=[
            "all_members_start",
            "per_member_rounds",
            "global_messages",
            "silent_not_persisted",
            "sequence_monotonic",
            "wake_excludes_speaker",
            "no_cancel_in_flight",
        ],
        category="group",
    ),

    EvalCase(
        id="G02",
        kind="invariant",
        scenario="four_two_silent",
        turns=["有人吗？"],
        description="4 人里两位沉默：沉默消耗轮次但不落库、不推送",
        expected_tools=None,
        invariants=[
            "all_members_start",
            "per_member_rounds",
            "silent_not_persisted",
            "global_messages",
            "sequence_monotonic",
        ],
        category="group",
    ),

    EvalCase(
        id="G03",
        kind="invariant",
        scenario="five_members",
        turns=["开会了，都说说"],
        description="5 人：K 降到 2（开口的两人各用满两轮；全局 M 由 G01/G04 覆盖）",
        expected_tools=None,
        invariants=["all_members_start", "per_member_rounds", "global_messages"],
        category="group",
    ),

    EvalCase(
        id="G04",
        kind="invariant",
        scenario="two_turns",
        turns=["第一件事：你们怎么看？", "第二件事呢？"],
        description="新用户消息 → K/M 重置且全员被唤醒",
        expected_tools=None,
        invariants=[
            "new_turn_resets",
            "per_member_rounds",
            "global_messages",
            "sequence_monotonic",
        ],
        category="group",
    ),

    EvalCase(
        id="G05",
        kind="invariant",
        scenario="slow_members",
        turns=["慢慢说，别急"],
        description="慢生成交错完成：在途轮次从不被取消",
        expected_tools=None,
        invariants=["no_cancel_in_flight", "all_members_start"],
        category="group",
    ),

    EvalCase(
        id="G06",
        kind="invariant",
        scenario="interjected",
        turns=["先说说你们的看法", "等一下，我补充一句"],
        description="用户插话：旧的思考说完（不取消），插话后开启新一轮且 K/M 重置",
        expected_tools=None,
        invariants=[
            "no_cancel_in_flight",
            "new_turn_resets",
            "per_member_rounds",
            "global_messages",
            "sequence_monotonic",
            "silent_not_persisted",
        ],
        category="group",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: vision (2 cases, ticket 09) —— **live**：默认不跑，--live 才跑
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="V01",
        query="这张图里写的是什么词？只回答你看到的那个词。",
        description="看图答题：回答必须基于图中内容（图里写着 SHADOWBOT）",
        expected_tools=None,
        image_fixture="vision_word",
        answer_should_contain=["shadowbot"],
        live=True,
        category="vision",
    ),

    EvalCase(
        id="V02",
        kind="recovery",
        query="帮我看看我的屏幕上写了什么？",
        description="视觉不可用（熔断）→ 必须真的尝试去看、失败后如实说没看成（措辞交给人工/评分模型），不编造屏幕内容",
        expected_tools=["see_screen"],
        fail_tools={"see_screen": "circuit_open"},
        # ticket #76：性质 2（工具被尝试且失败 + 回答了）取代关键词闭集；
        # "有没有编造屏幕内容"只有语义判断能定 → needs_review。
        must_report_not_completed=True,
        needs_review=True,
        needs_review_reason="回答是否如实说明「没看到画面」、没有编造屏幕内容需要语义判断（评分模型/人工），不列同义词",
        live=True,
        category="vision",
    ),

    # ═══════════════════════════════════════════════════════════════════
    # Category: search (3 cases, ticket 09) —— **live**：要真联网
    # 全链失败的那一条在 recovery 里（R03/R04：注入故障、零网络、不编造来源）。
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="S01",
        query="查一下 Python 3.14 是什么时候发布的，并给出来源链接。",
        description="原生搜索：答案必须带**真实域名**的来源（python.org）",
        expected_tools=None,
        must_cite=True,
        must_cite_hosts=["python.org"],
        answer_should_contain_any=["python"],
        live=True,
        category="search",
    ),

    EvalCase(
        id="S02",
        query="帮我查一下 Rust 编程语言的官方网站是哪个，给出链接。",
        description="原生搜索：来源必须是 rust-lang.org（或子域），不是编的链接",
        expected_tools=None,
        must_cite=True,
        must_cite_hosts=["rust-lang.org"],
        answer_should_contain_any=["rust"],
        live=True,
        category="search",
    ),

    EvalCase(
        id="S03",
        query="查一下 Rust 最新稳定版是哪个版本，并给出来源。",
        description="原生搜索（时效性）：答案要带**可信来源集合**里的来源（官方站点/发布追踪站，如 releases.rs）",
        expected_tools=None,
        must_cite=True,
        # 声明的可信来源集合（域名后缀匹配）：只认"某个 https://" 太弱，认死一个
        # rust-lang.org 又会把 releases.rs 这类等价来源判死 —— 用显式声明的集合。
        must_cite_hosts=[
            "rust-lang.org",
            "releases.rs",
            "rust-lang.github.io",
            "doc.rust-lang.org",
        ],
        answer_should_contain_any=["rust"],
        live=True,
        category="search",
    ),
]
