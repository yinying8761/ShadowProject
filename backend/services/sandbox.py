"""
Subprocess-based code execution sandbox.
Placeholder for Phase 3 implementation.
"""
import asyncio


class SandboxService:
    """Executes code in isolated subprocesses with timeouts."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def execute_python(self, code: str) -> str:
        """Execute Python code in a subprocess. Not yet implemented."""
        raise NotImplementedError("Python sandbox — Phase 3")

    async def execute_shell(self, command: str) -> str:
        """Execute a shell command. Not yet implemented."""
        raise NotImplementedError("Shell sandbox — Phase 3")
