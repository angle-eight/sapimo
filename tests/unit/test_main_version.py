from click.testing import CliRunner

from sapimo import __version__
from sapimo import main as cli_main


def test_version_option_prints_package_version():
    result = CliRunner().invoke(cli_main.main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output
