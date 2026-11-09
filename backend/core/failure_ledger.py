"""失败账本：同一个工具连续失败 2 次就本轮停用（ticket #71 / spec recovery-give-up §a）。

为什么必须是**硬限制**：实测模型不会自己停手 —— recovery 子集里 `read_file` 被调了 5 次、
`research` 被调了 4 次，prompt 里的软提示完全没拦住。所以"这个工具不行了"得由主循环记账。

规则（全部在这里，纯逻辑、可单测）：

- 键是**工具名**，记的是**连续**失败；该工具任意一次成功即清零；
- 连续失败达到 `limit`（默认 2）→ 本轮停用该工具（后续轮次不再把它给模型，调用也被拦截）；
- 停用之后最多再换 `substitutes`（默认 1）个**不同**工具；替代工具也失败 → **立即停止**；
- **用户拒绝审批不算工具故障**：不计入账本，但该工具本轮停用（不重试同一调用）。

账本只描述"这一轮的工具健康状况"，不碰 `ToolRuntime` 的错误信封、不改工具签名。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FailureLedger:
    """一轮对话内的工具失败账本。"""

    #: 连续失败多少次就停用该工具
    limit: int = 2
    #: 停用之后还允许换几个不同的工具（替代也失败就立即停止）
    substitutes: int = 1

    _streak: dict[str, int] = field(default_factory=dict)
    #: 工具 → 停用原因（"连续失败 2 次" / "用户拒绝审批" / "替补工具也失败"）
    _disabled: dict[str, str] = field(default_factory=dict)
    _substitutes_used: int = 0
    #: 当前正在当"替补"的那个工具（它一失败就立即停）
    _active_substitute: str | None = None
    _gave_up: bool = False

    # ── 查询 ──────────────────────────────────────────────────────────

    def is_disabled(self, tool: str) -> bool:
        return tool in self._disabled

    def available(self, tools: list[str]) -> list[str]:
        """过滤掉本轮已停用的工具（下一轮不要把坏工具再发给模型）。"""
        return [t for t in tools if t not in self._disabled]

    @property
    def should_stop(self) -> bool:
        """是否该停止尝试（替代工具也失败了）。"""
        return self._gave_up

    @property
    def disabled_tools(self) -> dict[str, str]:
        return dict(self._disabled)

    @property
    def streaks(self) -> dict[str, int]:
        return dict(self._streak)

    def describe(self) -> str:
        """一行日志/收尾话术用的摘要。"""
        if not self._disabled:
            return "无失败"
        parts = [f"{tool}（{reason}）" for tool, reason in self._disabled.items()]
        return "、".join(parts)

    # ── 记账 ──────────────────────────────────────────────────────────

    def note_tool_started(self, tool: str) -> bool:
        """开跑前登记：停用之后第一个**不同**工具就是这个回合的"替补"。

        返回 False 表示这个工具本轮已停用、不该执行（调用方应直接拦下）。
        """
        if tool in self._disabled:
            return False
        if (
            self._active_substitute is None
            and self._disabled
            and self._substitutes_used < self.substitutes
        ):
            self._active_substitute = tool
            self._substitutes_used += 1
        return True

    def record_success(self, tool: str) -> None:
        """成功：该工具的连续失败清零；若它是替补，则替补成功、恢复正常。"""
        self._streak.pop(tool, None)
        if tool == self._active_substitute:
            self._active_substitute = None

    def record_failure(self, tool: str) -> None:
        """工具失败（`ToolRuntime` 的错误信封 / 超时 / 熔断都算）。"""
        if tool in self._disabled:
            return
        if tool == self._active_substitute:
            # 换了工具还是失败 → 立即停，不再试
            self._gave_up = True
            self._disabled[tool] = "替补工具也失败"
            return
        streak = self._streak.get(tool, 0) + 1
        self._streak[tool] = streak
        if streak >= self.limit:
            self._disabled[tool] = f"连续失败 {streak} 次"

    def record_denial(self, tool: str) -> None:
        """用户拒绝审批：**不计入**失败账本，但该工具本轮停用。"""
        self._streak.pop(tool, None)
        self._disabled.setdefault(tool, "用户拒绝审批")
