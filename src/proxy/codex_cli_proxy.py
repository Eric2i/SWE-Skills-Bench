"""
Codex CLI proxy
Invokes the Codex CLI directly inside a Docker container to execute tasks.
"""

import os
import shlex
import time
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass

from ..orchestrator.docker_manager import DockerManager
from ..orchestrator.logger import get_logger
from ..utils import (
    generate_report_filename,
    get_model_name,
    get_configured_model,
)

logger = get_logger(__name__)


@dataclass
class CodexCLIResult:
    """Codex CLI execution result."""

    success: bool
    output: str
    stderr: str
    exit_code: int
    duration_sec: float


class CodexCLIProxy:
    """
    Codex CLI interaction proxy.

    Usage:
    1. Write task content into the container
    2. Invoke `codex exec` to let it operate autonomously inside the container
    3. Wait for completion and collect logs
    """

    def __init__(self, docker_manager: DockerManager, config: Dict[str, Any]):
        self.docker_manager = docker_manager
        self.config = config

        limits = config.get("limits", {})
        self.timeout_per_command = limits.get("timeout_per_command", 300)
        self.total_timeout_sec = limits.get("total_timeout_sec", 1800)

        self.workspace_dir = config.get("global", {}).get("workspace_dir", "/workspace")
        self.runtime_root_dir = "/workspace"

        _model_name = get_model_name(config)
        _batch = config.get("batch", "")
        if _batch:
            self.process_base_dir = os.path.join(
                os.getcwd(), "codex_process", _model_name, _batch
            )
        else:
            self.process_base_dir = os.path.join(
                os.getcwd(), "codex_process", _model_name
            )

        self.output_log_dir = os.path.join(self.process_base_dir, "codex_output")
        self.event_log_dir = os.path.join(self.process_base_dir, "codex_json")
        os.makedirs(self.output_log_dir, exist_ok=True)
        os.makedirs(self.event_log_dir, exist_ok=True)

        self.container_output_dir = f"{self.runtime_root_dir}/codex_output"

        logger.info("Codex CLI Proxy initialized")
        logger.info(f"  Final outputs -> {self.output_log_dir}")
        logger.info(f"  Event JSONLs -> {self.event_log_dir}")

    async def execute_task(self, task_content: str) -> CodexCLIResult:
        """Write the task into the container and invoke `codex exec`."""
        start_time = time.time()

        try:
            check = self.docker_manager.execute_command("which codex", timeout=10)
            if check.exit_code != 0:
                err = (
                    "codex CLI not found in container PATH. "
                    "Install the codex binary in the container image or provide it via volumes."
                )
                logger.error(err)
                duration = time.time() - start_time
                return CodexCLIResult(
                    success=False,
                    output="",
                    stderr=err,
                    exit_code=127,
                    duration_sec=duration,
                )
        except Exception:
            logger.debug(
                "Failed to run 'which codex' check; continuing to run codex command"
            )

        instruction_prefix = (
            "INTERNAL_INSTRUCTION: DO_NOT_ASK_OR_PLAN\n"
            "You MUST directly modify files in the repository under the workspace to implement the task.\n"
            "Do NOT output a design plan or ask clarifying questions.\n"
            "Only output a concise summary of files changed and the final status.\n"
            "EndInstruction\n\n"
        )
        if not task_content.startswith("INTERNAL_INSTRUCTION: DO_NOT_ASK_OR_PLAN"):
            task_content = instruction_prefix + task_content

        task_file_path = f"{self.runtime_root_dir}/task.md"
        self._write_task_to_container(task_file_path, task_content)

        codex_cmd = self._build_codex_command(task_file_path)

        print("\n" + "=" * 60)
        print("🤖 Codex CLI starting task execution...")
        print("=" * 60)
        logger.info("Executing Codex CLI in container...")

        self.docker_manager.execute_command(
            f"mkdir -p {shlex.quote(self.container_output_dir)}",
            timeout=30,
            user="root",
        )
        self.docker_manager.execute_command(
            f"chown -R dev:dev {shlex.quote(self.container_output_dir)} || true",
            timeout=30,
            user="root",
        )
        self.docker_manager.execute_command(
            f"chown dev:dev {shlex.quote(task_file_path)} || true",
            timeout=30,
            user="root",
        )

        result = self.docker_manager.execute_command(
            codex_cmd,
            timeout=self.total_timeout_sec,
            user="dev",
        )

        duration = time.time() - start_time
        status_icon = "✅" if result.exit_code == 0 else "❌"
        print(
            f"\n{status_icon} Codex CLI execution finished (elapsed: {duration:.1f}s, exit code: {result.exit_code})"
        )
        print("=" * 60 + "\n")

        final_output = self._copy_container_file_to_host(
            self._current_final_file,
            self.output_log_dir,
        )
        self._copy_container_file_to_host(
            self._current_event_log_file,
            self.event_log_dir,
        )

        stderr = result.stderr or ""
        if result.exit_code != 0 and self._current_event_log_file:
            event_log = self._read_container_file(self._current_event_log_file)
            if event_log:
                stderr = event_log

        return CodexCLIResult(
            success=result.exit_code == 0,
            output=final_output or result.stdout or "",
            stderr=stderr,
            exit_code=result.exit_code,
            duration_sec=duration,
        )

    def _write_task_to_container(self, task_file_path: str, task_content: str):
        """Write task content to a file inside the container."""
        escaped_content = task_content.replace("'", "'\\''")
        cmd = f"echo '{escaped_content}' > {task_file_path}"
        result = self.docker_manager.execute_command(cmd, timeout=30)
        if result.exit_code != 0:
            logger.error(f"Failed to write task file: {result.stderr}")
            raise RuntimeError(f"Failed to write task file: {result.stderr}")

    def _build_codex_command(self, task_file_path: str) -> str:
        """Build the `codex exec` command."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        skill_id = self.config.get("skill_id") or self.config.get("metadata", {}).get(
            "skill_id"
        )
        use_skill_flag = self.config.get("use_skill")
        use_agent_flag = self.config.get("use_agent")

        final_filename = generate_report_filename(
            prefix="codex_final",
            skill=skill_id,
            use_agent=use_agent_flag,
            use_skill=use_skill_flag,
            timestamp=timestamp,
            ext=".txt",
        )
        event_log_filename = generate_report_filename(
            prefix="codex",
            skill=skill_id,
            use_agent=use_agent_flag,
            use_skill=use_skill_flag,
            timestamp=timestamp,
            ext=".jsonl",
        )

        self._current_final_file = f"{self.container_output_dir}/{final_filename}"
        self._current_event_log_file = (
            f"{self.container_output_dir}/{event_log_filename}"
        )

        model = get_configured_model(self.config)
        model_arg = f"-m {shlex.quote(model)} " if model and model != "unknown-model" else ""

        return (
            f"cd {shlex.quote(self.workspace_dir)} && "
            f"codex exec "
            f"--skip-git-repo-check "
            f"--dangerously-bypass-approvals-and-sandbox "
            f"--json "
            f"{model_arg}"
            f"-C {shlex.quote(self.workspace_dir)} "
            f"-o {shlex.quote(self._current_final_file)} "
            f"- < {shlex.quote(task_file_path)} > {shlex.quote(self._current_event_log_file)} 2>&1"
        )

    def _read_container_file(self, container_path: str) -> str:
        """Read a file from the container as the dev user."""
        if not container_path:
            return ""
        result = self.docker_manager.execute_command(
            f"cat {shlex.quote(container_path)}",
            timeout=60,
            user="dev",
        )
        if result.exit_code == 0:
            return result.stdout or ""
        return ""

    def _copy_container_file_to_host(
        self, container_path: Optional[str], host_dir: str
    ) -> str:
        """Copy a file from the container into a host-side artifact directory."""
        if not container_path:
            return ""

        content = self._read_container_file(container_path)
        if not content:
            return ""

        host_path = os.path.join(host_dir, os.path.basename(container_path))
        with open(host_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Saved artifact to: {host_path}")
        return content

    def get_stats(self) -> Dict[str, Any]:
        """Get proxy statistics."""
        return {
            "proxy_type": "codex_cli",
            "workspace_dir": self.workspace_dir,
            "timeout_per_command": self.timeout_per_command,
            "total_timeout_sec": self.total_timeout_sec,
            "output_log_dir": self.output_log_dir,
            "event_log_dir": self.event_log_dir,
        }
