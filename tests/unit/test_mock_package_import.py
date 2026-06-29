import importlib
import sys


def test_import_sapimo_mock_without_config_does_not_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    sys.modules.pop("sapimo.mock", None)
    sys.modules.pop("sapimo.mock.initialize", None)

    module = importlib.import_module("sapimo.mock")

    assert hasattr(module, "api")
    assert hasattr(module, "change_input")
    assert hasattr(module, "options")
    assert not hasattr(module, "legacy_api")


def test_access_legacy_api_is_removed():
    import sapimo.mock as mock_pkg

    try:
        _ = mock_pkg.legacy_api
        assert False, "legacy_api should be removed"
    except AttributeError:
        pass
