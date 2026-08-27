"""
Hand-annotated eval cases for Agent tool-calling accuracy.

15 cases across 5 categories:
- tool_selection (4): Does the Agent pick the right tool?
- argument_accuracy (3): Are the arguments correct?
- answer_quality (3): Is the final response accurate and well-formed?
- edge_case (3): No-tool queries, multi-tool chains, ambiguous intents.
- memory (2): Memory save and recall via save_memory / search_memory.
"""

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    """A single hand-annotated test case for Agent evaluation."""

    id: str                                     # unique identifier, e.g. "T01"
    query: str                                  # user input
    description: str                            # what this case tests
    expected_tools: list[str] = field(default_factory=list)   # expected tool names (order-sensitive for chains)
    expected_args_contain: dict[str, list[str]] = field(default_factory=dict)  # tool_name → substrings in args JSON
    answer_should_contain: list[str] = field(default_factory=list)    # keywords that MUST ALL appear (AND)
    answer_should_contain_any: list[str] = field(default_factory=list)  # keywords where ANY must appear (OR); empty = always true
    answer_should_not_contain: list[str] = field(default_factory=list)  # keywords that MUST NOT appear (NOT)
    category: str = "tool_selection"            # tool_selection | argument_accuracy | answer_quality | edge_case | memory


EVAL_CASES: list[EvalCase] = [
    # ═══════════════════════════════════════════════════════════════════
    # Category: tool_selection (4 cases)
    # ═══════════════════════════════════════════════════════════════════

    EvalCase(
        id="T01",
        query="现在是什么时间？几点了？",
        description="时间查询 → 应调用 get_current_time，不应调用 research",
        expected_tools=["get_current_time"],
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
        description="网页搜索 → 应调用 research",
        expected_tools=["research"],
        expected_args_contain={"research": ["Python"]},
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
        description="自我介绍 → 回答应包含身份/角色关键词（伙伴/朋友/助手等）",
        expected_tools=[],  # self-intro shouldn't need tools
        answer_should_contain=[],
        answer_should_contain_any=["伙伴", "朋友", "助手"],
        answer_should_not_contain=["error", "错误"],
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
]
