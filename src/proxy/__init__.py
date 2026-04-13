"""
Agent Skills Benchmark - Proxy Module
Module C: Agent interaction proxy, responsible for secure sandbox execution.
Supports multiple CLI-driven coding agents, including Claude Code and Codex.
"""

from ..utils import get_agent_backend
from .claude_code_proxy import ClaudeCodeProxy, ClaudeCodeResult
from .codex_cli_proxy import CodexCLIProxy, CodexCLIResult

__all__ = [
    "ClaudeCodeProxy",
    "ClaudeCodeResult",
    "CodexCLIProxy",
    "CodexCLIResult",
    "create_agent_proxy",
]


def create_agent_proxy(docker_manager, config):
    """Instantiate the configured agent proxy."""
    backend = get_agent_backend(config)
    if backend == "codex":
        return CodexCLIProxy(docker_manager=docker_manager, config=config)
    if backend == "claude":
        return ClaudeCodeProxy(docker_manager=docker_manager, config=config)
    raise ValueError(f"Unsupported agent backend: {backend}")
