"""Config is env-driven; reconstruct it under monkeypatched environments to
exercise every default_factory and property branch."""
from cascade.config import CONFIG, Config

_ENV = (
    "ANTHROPIC_API_KEY", "CASCADE_ENABLE_CLOUD", "CASCADE_SKIP_NPU",
    "CASCADE_CLOUD_MAX_CALLS", "CASCADE_CLOUD_USD", "CASCADE_NPU_MODEL_DIR",
    "CASCADE_LOG", "OLLAMA_BASE_URL", "CASCADE_GPU_MODEL", "CASCADE_CLOUD_MODEL",
)


def _clean(mp):
    for k in _ENV:
        mp.delenv(k, raising=False)


def test_module_singleton_exists():
    assert isinstance(CONFIG, Config)


def test_defaults(monkeypatch):
    _clean(monkeypatch)
    c = Config()
    assert c.anthropic_api_key is None
    assert c.enable_cloud is False
    assert c.cloud_enabled is False          # and-branch: left False
    assert c.npu_device_order == ("NPU", "GPU.0", "CPU")  # else arm
    assert c.cloud_max_calls == 3
    assert c.cloud_usd_budget == 0.50
    assert c.npu_model_dir.endswith("qwen2.5-coder-1.5b-npu")
    assert c.log_path.endswith("cascade.log")


def test_skip_npu_branch(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("CASCADE_SKIP_NPU", "1")
    assert Config().npu_device_order == ("GPU.0", "CPU")  # if arm


def test_cloud_enabled_true(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CASCADE_ENABLE_CLOUD", "1")
    c = Config()
    assert c.anthropic_api_key == "sk-test"
    assert c.enable_cloud is True
    assert c.cloud_enabled is True           # and-branch: both True


def test_cloud_disabled_when_key_present_but_not_enabled(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Config().cloud_enabled is False   # left False, key present


def test_cloud_disabled_when_enabled_but_no_key(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("CASCADE_ENABLE_CLOUD", "1")
    assert Config().cloud_enabled is False   # left True, right False


def test_credit_guard_env_overrides(monkeypatch):
    # Only default_factory fields re-read env per Config(); the credit-guard
    # knobs are the per-run ones that matter. (npu_model_dir / log_path / etc.
    # are plain class defaults, frozen at import — correct for real use, where
    # the env is set before the process starts.)
    _clean(monkeypatch)
    monkeypatch.setenv("CASCADE_CLOUD_MAX_CALLS", "7")
    monkeypatch.setenv("CASCADE_CLOUD_USD", "1.25")
    c = Config()
    assert c.cloud_max_calls == 7
    assert c.cloud_usd_budget == 1.25
