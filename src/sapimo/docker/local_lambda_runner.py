"""Local Lambda runner for single-container Sapimo mode."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import os
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sapimo.docker.lambda_execution_logger import LambdaExecutionLogger
from sapimo.mock.api import monkeypatch


class LocalLambdaRunner:
    """Execute lambda handlers in-process with scoped env/sys.path."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self._venvs_root = project_root / "api_mock" / ".lambda_venvs"
        # In-process execution mutates os.environ and sys.path temporarily.
        # Serialize executions to avoid cross-request contamination.
        self._execution_lock = asyncio.Lock()
        self._logger = LambdaExecutionLogger(project_root / "api_mock" / "log")

    async def execute(
        self, route_info: dict[str, Any], event: dict[str, Any]
    ) -> dict[str, Any]:
        handler = route_info.get("handler", "app.lambda_handler")
        module_name, func_name = handler.rsplit(".", 1)
        function_name = route_info.get("function_name", module_name)

        code_path = self._resolve_project_path(route_info.get("code_uri", "./"))
        layer_paths = [
            self._resolve_project_path(layer) for layer in route_info.get("layers", [])
        ]

        python_paths = [str(code_path)]

        # Container Lambda: install pip packages into a dedicated venv and add site-packages
        pip_packages = route_info.get("pip_packages", [])
        if pip_packages:
            site_packages = self._ensure_lambda_venv(function_name, pip_packages)
            # venv site-packages take priority so they shadow sapimo's own deps only
            # when a direct import conflict would occur; code_uri itself stays first
            # so user code wins over its own installed packages.
            python_paths.append(str(site_packages))

        for layer_path in layer_paths:
            python_paths.append(str(layer_path))
            python_paths.append(str(layer_path / "python"))

        env = self._build_lambda_environment(route_info)

        log_file = self._logger.get_log_file(function_name, code_path, module_name)

        async with self._execution_lock:
            with self._temporary_environ(env), self._temporary_syspath(python_paths):
                sys.modules.pop(module_name, None)
                module = importlib.import_module(module_name)

                if not hasattr(module, func_name):
                    raise RuntimeError(
                        f"Lambda entrypoint '{func_name}' not found in module '{module_name}'"
                    )

                handler_func = getattr(module, func_name)

                start = time.perf_counter()
                error_text = None
                result = None
                with self._logger.capture_stdout() as captured:
                    try:
                        with monkeypatch.apply():
                            result = handler_func(event, None)
                            if inspect.isawaitable(result):
                                result = await result
                    except Exception:
                        error_text = traceback.format_exc()
                        raise
                    finally:
                        duration_ms = (time.perf_counter() - start) * 1000
                        self._logger.log_execution(
                            log_file=log_file,
                            function_name=function_name,
                            handler=handler,
                            event=event,
                            result=result
                            if isinstance(result, dict)
                            else (
                                {"statusCode": 200, "body": result}
                                if result is not None
                                else None
                            ),
                            captured_output=captured.getvalue(),
                            duration_ms=duration_ms,
                            error=error_text,
                        )

                if isinstance(result, dict):
                    return result
                return {"statusCode": 200, "body": result}

    def _build_lambda_environment(self, route_info: dict[str, Any]) -> dict[str, str]:
        env = dict(os.environ)
        route_env = route_info.get("environment", {})
        env.update({k: str(v) for k, v in route_env.items()})

        configured_region = (
            env.get("AWS_DEFAULT_REGION") or env.get("AWS_REGION") or "us-east-1"
        )
        env["AWS_REGION"] = configured_region
        env["AWS_DEFAULT_REGION"] = configured_region
        env.setdefault("AWS_ACCESS_KEY_ID", "testing")
        env.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        env.setdefault("AWS_SESSION_TOKEN", "testing")
        env.setdefault("AWS_EC2_METADATA_DISABLED", "true")

        return env

    def _ensure_lambda_venv(self, function_name: str, pip_packages: list[str]) -> Path:
        """Return the site-packages path for a per-Lambda venv.

        Creates (or re-creates) the venv only when the package list changes.
        Uses a hash file to detect changes cheaply.
        """
        venv_dir = self._venvs_root / function_name / "venv"
        hash_file = self._venvs_root / function_name / "packages.hash"

        packages_hash = hashlib.sha256(
            "\n".join(sorted(pip_packages)).encode()
        ).hexdigest()

        if hash_file.exists() and hash_file.read_text().strip() == packages_hash:
            # Package list unchanged — reuse existing venv
            return self._site_packages_path(venv_dir)

        # Create or re-create the venv
        if venv_dir.exists():
            import shutil

            shutil.rmtree(venv_dir)

        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )

        venv_python = venv_dir / "bin" / "python"
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", *pip_packages],
            check=True,
            capture_output=True,
        )

        hash_file.write_text(packages_hash)
        return self._site_packages_path(venv_dir)

    @staticmethod
    def _site_packages_path(venv_dir: Path) -> Path:
        """Locate the site-packages directory inside a venv."""
        lib_dir = venv_dir / "lib"
        # lib/pythonX.Y/site-packages
        for child in lib_dir.iterdir():
            candidate = child / "site-packages"
            if candidate.is_dir():
                return candidate
        raise RuntimeError(f"site-packages not found under {venv_dir}")

    def _resolve_project_path(self, target_path: str) -> Path:
        path = Path(target_path)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    @contextmanager
    def _temporary_syspath(self, paths: list[str]):
        added: list[str] = []
        for p in paths:
            if p not in sys.path:
                sys.path.insert(0, p)
                added.append(p)

        try:
            yield
        finally:
            for p in added:
                if p in sys.path:
                    sys.path.remove(p)

    @contextmanager
    def _temporary_environ(self, new_env: dict[str, str]):
        old_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(new_env)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(old_env)
