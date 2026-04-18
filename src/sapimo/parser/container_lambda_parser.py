"""Minimal Dockerfile parser for container-type Lambda functions.

Extracts only the information sapimo needs to execute container Lambda
functions in-process:
  - Handler (from CMD)
  - pip packages (from RUN pip install ...)
  - Environment variables (from ENV)

All other Dockerfile directives (FROM, COPY, ADD, ENTRYPOINT, RUN other than
pip, etc.) are intentionally ignored.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)

# Matches: pip install, pip3 install, python -m pip install, python3 -m pip install
_PIP_INSTALL_RE = re.compile(
    r"^(?:python3?)\s+-m\s+pip\s+install|^pip3?\s+install", re.IGNORECASE
)


@dataclass
class ContainerLambdaInfo:
    handler: str
    pip_packages: list[str] = field(default_factory=list)
    envs: dict[str, str] = field(default_factory=dict)


class ContainerLambdaDockerfileParser:
    """Parse a Dockerfile and extract only what sapimo needs for in-process Lambda execution."""

    def __init__(self, docker_context: Path, dockerfile_name: str = "Dockerfile"):
        self._context = docker_context
        self._dockerfile = docker_context / dockerfile_name

    def parse(self) -> ContainerLambdaInfo:
        if not self._dockerfile.exists():
            raise FileNotFoundError(f"Dockerfile not found: {self._dockerfile}")

        raw_lines = self._dockerfile.read_text(encoding="utf-8").splitlines()
        logical_lines = self._join_continuations(raw_lines)

        handler = ""
        pip_packages: list[str] = []
        envs: dict[str, str] = {}

        for line in logical_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            directive, _, rest = line.partition(" ")
            directive = directive.upper()
            rest = rest.strip()

            if directive == "ENV":
                envs.update(self._parse_env(rest))
            elif directive == "RUN":
                packages = self._extract_pip_packages(rest, envs)
                pip_packages.extend(packages)
            elif directive == "CMD":
                handler = self._parse_cmd(rest)
            # FROM, COPY, ADD, ENTRYPOINT, WORKDIR, ARG, EXPOSE, LABEL, etc. → ignored

        if not handler:
            logger.warning(
                "Dockerfile '%s' has no CMD directive. "
                "Defaulting handler to 'app.lambda_handler'. "
                "If your Lambda uses a different handler, edit api_mock/config.yaml manually.",
                self._dockerfile,
            )
            handler = "app.lambda_handler"

        return ContainerLambdaInfo(
            handler=handler,
            pip_packages=pip_packages,
            envs=envs,
        )

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _join_continuations(lines: list[str]) -> list[str]:
        """Merge backslash-continued lines into single logical lines."""
        result: list[str] = []
        buf = ""
        for line in lines:
            if line.rstrip().endswith("\\"):
                buf += line.rstrip()[:-1] + " "
            else:
                result.append(buf + line)
                buf = ""
        if buf:
            result.append(buf)
        return result

    @staticmethod
    def _parse_env(rest: str) -> dict[str, str]:
        """Parse ENV directive into a key-value dict.

        Supports both forms:
          ENV KEY=VALUE KEY2=VALUE2
          ENV KEY VALUE   (single key legacy form)
        """
        envs: dict[str, str] = {}
        rest = rest.strip()

        # Determine form: if first token contains '=' it's key=value form
        if "=" in rest.split()[0] if rest else "":
            # key=value form — potentially multiple pairs
            # Use shlex to handle quoting correctly
            try:
                tokens = shlex.split(rest)
            except ValueError:
                tokens = rest.split()
            for token in tokens:
                if "=" in token:
                    k, _, v = token.partition("=")
                    envs[k.strip('"').strip("'")] = v.strip('"').strip("'")
        else:
            # Legacy: ENV KEY VALUE (only one pair, rest is the value)
            parts = rest.split(None, 1)
            if len(parts) == 2:
                envs[parts[0]] = parts[1].strip('"').strip("'")

        return envs

    def _extract_pip_packages(self, run_body: str, envs: dict[str, str]) -> list[str]:
        """Extract package specs from a RUN directive.

        Handles:
          pip install pkg1 pkg2
          pip install -r requirements.txt
          pip3 install --no-cache-dir pkg
          python -m pip install pkg
          Shell chains: && and ; (process only the pip parts)
        """
        packages: list[str] = []

        # Split on && and ; to handle chained commands
        sub_commands = re.split(r"&&|;", run_body)
        for cmd in sub_commands:
            cmd = cmd.strip()
            if not _PIP_INSTALL_RE.match(cmd):
                continue
            packages.extend(self._parse_pip_install_args(cmd, envs))

        return packages

    def _parse_pip_install_args(self, cmd: str, envs: dict[str, str]) -> list[str]:
        """Parse arguments of a single `pip install ...` command."""
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()

        # Strip the `pip install` / `python -m pip install` prefix
        # Find the index of "install"
        try:
            install_idx = next(i for i, t in enumerate(tokens) if t == "install")
        except StopIteration:
            return []

        args = tokens[install_idx + 1 :]

        packages: list[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            # Flags with a separate value argument
            if arg in (
                "-i",
                "--index-url",
                "--extra-index-url",
                "-t",
                "--target",
                "--prefix",
                "-c",
                "--constraint",
                "--root",
                "--upgrade-strategy",
            ):
                skip_next = True
                continue
            # Flags without value
            if arg.startswith("-"):
                if arg in ("-r", "--requirement"):
                    skip_next = True
                    # Try to read the requirements file
                    # (next iteration handles the filename via _expand_requirements)
                    # We peek ahead to find the filename
                    idx = args.index(arg)
                    if idx + 1 < len(args):
                        req_file = args[idx + 1]
                        packages.extend(self._expand_requirements(req_file))
                continue
            packages.append(arg)

        return packages

    def _expand_requirements(self, req_filename: str) -> list[str]:
        """Read a requirements.txt file and return package spec lines."""
        req_path = self._context / req_filename
        if not req_path.exists():
            logger.warning(
                "requirements file '%s' not found in Docker context '%s'. Skipping.",
                req_filename,
                self._context,
            )
            return []

        packages: list[str] = []
        for line in req_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Skip -r/-c nested references (uncommon, not worth the recursion)
            if line.startswith("-"):
                continue
            packages.append(line)
        return packages

    @staticmethod
    def _parse_cmd(rest: str) -> str:
        """Extract handler string from CMD directive.

        Accepts both exec form CMD ["module.handler"] and shell form CMD module.handler.
        Returns the first token which is expected to be the Lambda handler.
        """
        rest = rest.strip()
        if rest.startswith("["):
            # Exec form: ["app.lambda_handler"] or ["app.lambda_handler", "arg"]
            # Strip brackets and split by comma
            inner = rest.strip("[]")
            parts = [p.strip().strip('"').strip("'") for p in inner.split(",")]
            return parts[0] if parts else ""
        else:
            # Shell form: app.lambda_handler
            return rest.split()[0] if rest else ""
