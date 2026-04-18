"""Tests for ContainerLambdaDockerfileParser.

Verifies that ENV, CMD, and RUN pip install directives are correctly parsed,
and that everything else is silently ignored.
"""

import pytest
from pathlib import Path
from sapimo.parser.container_lambda_parser import ContainerLambdaDockerfileParser


# ------------------------------------------------------------------ #
# Helpers / fixtures                                                   #
# ------------------------------------------------------------------ #


@pytest.fixture()
def dockerfile_factory(tmp_path: Path):
    """
    Returns a callable that writes a Dockerfile (and optional sibling files)
    into tmp_path and returns a ContainerLambdaDockerfileParser pointed at it.

    Usage:
        parser = dockerfile_factory(lines, extra_files={"requirements.txt": "boto3\n"})
    """

    def _factory(
        lines: list[str],
        extra_files: dict[str, str] | None = None,
        dockerfile_name: str = "Dockerfile",
    ) -> ContainerLambdaDockerfileParser:
        dockerfile_path = tmp_path / dockerfile_name
        # Always prepend a FROM so the file is valid
        content = "FROM public.ecr.aws/lambda/python:3.12\n" + "\n".join(lines) + "\n"
        dockerfile_path.write_text(content, encoding="utf-8")

        if extra_files:
            for fname, ftext in extra_files.items():
                (tmp_path / fname).write_text(ftext, encoding="utf-8")

        return ContainerLambdaDockerfileParser(tmp_path, dockerfile_name)

    return _factory


# ------------------------------------------------------------------ #
# CMD parsing                                                          #
# ------------------------------------------------------------------ #


class TestCmdParsing:
    def test_exec_form_single(self, dockerfile_factory):
        parser = dockerfile_factory(['CMD ["app.lambda_handler"]'])
        info = parser.parse()
        assert info.handler == "app.lambda_handler"

    def test_exec_form_with_args_ignored(self, dockerfile_factory):
        parser = dockerfile_factory(['CMD ["mymodule.handler", "--unused-arg"]'])
        info = parser.parse()
        assert info.handler == "mymodule.handler"

    def test_shell_form(self, dockerfile_factory):
        parser = dockerfile_factory(["CMD app.lambda_handler"])
        info = parser.parse()
        assert info.handler == "app.lambda_handler"

    def test_no_cmd_defaults_to_app_lambda_handler(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN echo hello"])
        info = parser.parse()
        assert info.handler == "app.lambda_handler"

    def test_entrypoint_is_ignored(self, dockerfile_factory):
        """ENTRYPOINT must not be used as handler."""
        parser = dockerfile_factory(
            ['ENTRYPOINT ["entrypoint.handler"]', 'CMD ["app.real_handler"]']
        )
        info = parser.parse()
        assert info.handler == "app.real_handler"


# ------------------------------------------------------------------ #
# ENV parsing                                                          #
# ------------------------------------------------------------------ #


class TestEnvParsing:
    def test_key_value_form(self, dockerfile_factory):
        parser = dockerfile_factory(["ENV TABLE_NAME=items REGION=ap-northeast-1"])
        info = parser.parse()
        assert info.envs["TABLE_NAME"] == "items"
        assert info.envs["REGION"] == "ap-northeast-1"

    def test_quoted_value(self, dockerfile_factory):
        parser = dockerfile_factory(['ENV MY_NAME="John Doe"'])
        info = parser.parse()
        assert info.envs["MY_NAME"] == "John Doe"

    def test_legacy_form(self, dockerfile_factory):
        """ENV KEY VALUE (space-separated legacy form)."""
        parser = dockerfile_factory(["ENV VERSION 1.0"])
        info = parser.parse()
        assert info.envs["VERSION"] == "1.0"

    def test_multiple_env_lines(self, dockerfile_factory):
        parser = dockerfile_factory(["ENV KEY1=val1", "ENV KEY2=val2"])
        info = parser.parse()
        assert info.envs["KEY1"] == "val1"
        assert info.envs["KEY2"] == "val2"

    def test_no_env_returns_empty(self, dockerfile_factory):
        parser = dockerfile_factory(['CMD ["app.lambda_handler"]'])
        info = parser.parse()
        assert info.envs == {}


# ------------------------------------------------------------------ #
# RUN pip install parsing                                              #
# ------------------------------------------------------------------ #


class TestPipInstallParsing:
    def test_simple_packages(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN pip install boto3 requests"])
        info = parser.parse()
        assert "boto3" in info.pip_packages
        assert "requests" in info.pip_packages

    def test_pip3(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN pip3 install pydantic>=2.0"])
        info = parser.parse()
        assert "pydantic>=2.0" in info.pip_packages

    def test_python_m_pip(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN python -m pip install mypackage==1.2.3"])
        info = parser.parse()
        assert "mypackage==1.2.3" in info.pip_packages

    def test_python3_m_pip(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN python3 -m pip install mypackage"])
        info = parser.parse()
        assert "mypackage" in info.pip_packages

    def test_no_cache_dir_flag_skipped(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN pip install --no-cache-dir boto3"])
        info = parser.parse()
        assert "boto3" in info.pip_packages
        assert "--no-cache-dir" not in info.pip_packages

    def test_requirements_file_expanded(self, dockerfile_factory):
        parser = dockerfile_factory(
            ["RUN pip install -r requirements.txt"],
            extra_files={"requirements.txt": "requests>=2.28\nboto3==1.26.0\n"},
        )
        info = parser.parse()
        assert "requests>=2.28" in info.pip_packages
        assert "boto3==1.26.0" in info.pip_packages

    def test_requirements_file_comments_and_blanks_ignored(self, dockerfile_factory):
        req_content = "# comment\n\nboto3\n  # another comment\nrequests\n"
        parser = dockerfile_factory(
            ["RUN pip install -r requirements.txt"],
            extra_files={"requirements.txt": req_content},
        )
        info = parser.parse()
        assert info.pip_packages == ["boto3", "requests"]

    def test_chained_commands(self, dockerfile_factory):
        parser = dockerfile_factory(
            ["RUN apt-get update && pip install boto3 && pip3 install requests"]
        )
        info = parser.parse()
        assert "boto3" in info.pip_packages
        assert "requests" in info.pip_packages

    def test_non_pip_run_ignored(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN apt-get install -y libpq-dev"])
        info = parser.parse()
        assert info.pip_packages == []

    def test_no_pip_returns_empty(self, dockerfile_factory):
        parser = dockerfile_factory(['CMD ["app.lambda_handler"]'])
        info = parser.parse()
        assert info.pip_packages == []

    def test_backslash_continuation(self, dockerfile_factory):
        parser = dockerfile_factory(["RUN pip install \\\n    boto3 \\\n    requests"])
        info = parser.parse()
        assert "boto3" in info.pip_packages
        assert "requests" in info.pip_packages

    def test_missing_requirements_file_returns_empty(self, dockerfile_factory):
        """Missing -r file should warn and return empty, not raise."""
        parser = dockerfile_factory(["RUN pip install -r missing_reqs.txt"])
        info = parser.parse()
        assert info.pip_packages == []


# ------------------------------------------------------------------ #
# Edge cases                                                           #
# ------------------------------------------------------------------ #


class TestEdgeCases:
    def test_dockerfile_not_found_raises(self, tmp_path: Path):
        parser = ContainerLambdaDockerfileParser(tmp_path, "NonExistent")
        with pytest.raises(FileNotFoundError):
            parser.parse()

    def test_comments_ignored(self, dockerfile_factory):
        parser = dockerfile_factory(
            ["# this is a comment", 'CMD ["app.lambda_handler"]']
        )
        info = parser.parse()
        assert info.handler == "app.lambda_handler"

    def test_combined_all_directives(self, dockerfile_factory):
        parser = dockerfile_factory(
            [
                "ENV TABLE_NAME=events",
                "RUN pip install boto3 pydantic>=2.0",
                'CMD ["events.handler"]',
            ]
        )
        info = parser.parse()
        assert info.handler == "events.handler"
        assert "boto3" in info.pip_packages
        assert "pydantic>=2.0" in info.pip_packages
        assert info.envs["TABLE_NAME"] == "events"
