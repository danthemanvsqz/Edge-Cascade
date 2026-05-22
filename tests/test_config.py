"""Config is env-driven; reconstruct it under a controlled environment
(via pytest-mock's mocker.patch.dict) to exercise every default_factory and
property branch."""
import os

from cascade.config import CONFIG, Config


def _env(mocker, **overrides):
    """Replace os.environ with exactly `overrides` for the test."""
    mocker.patch.dict(os.environ, overrides, clear=True)


def test_module_singleton_exists():
    assert isinstance(CONFIG, Config)


def test_defaults(mocker):
    _env(mocker)
    c = Config()
    assert c.anthropic_api_key is None
    assert c.enable_cloud is False
    assert c.cloud_enabled is False          # and-branch: left False
    assert c.npu_device_order == ("NPU", "GPU.0", "CPU")  # else arm
    assert c.cloud_max_calls == 3
    assert c.cloud_usd_budget == 0.50
    assert c.npu_model_dir.endswith("qwen2.5-coder-1.5b-npu")
    assert c.log_path.endswith("cascade.log")


def test_skip_npu_branch(mocker):
    _env(mocker, CASCADE_SKIP_NPU="1")
    assert Config().npu_device_order == ("GPU.0", "CPU")  # if arm


def test_cloud_enabled_true(mocker):
    _env(mocker, ANTHROPIC_API_KEY="sk-test", CASCADE_ENABLE_CLOUD="1")
    c = Config()
    assert c.anthropic_api_key == "sk-test"
    assert c.enable_cloud is True
    assert c.cloud_enabled is True           # and-branch: both True


def test_cloud_disabled_when_key_present_but_not_enabled(mocker):
    _env(mocker, ANTHROPIC_API_KEY="sk-test")
    assert Config().cloud_enabled is False   # left False, key present


def test_cloud_disabled_when_enabled_but_no_key(mocker):
    _env(mocker, CASCADE_ENABLE_CLOUD="1")
    assert Config().cloud_enabled is False   # left True, right False


def test_credit_guard_env_overrides(mocker):
    # Only default_factory fields re-read env per Config(); the credit-guard
    # knobs are the per-run ones that matter. (npu_model_dir / log_path / etc.
    # are plain class defaults, frozen at import — correct for real use, where
    # the env is set before the process starts.)
    _env(mocker, CASCADE_CLOUD_MAX_CALLS="7", CASCADE_CLOUD_USD="1.25")
    c = Config()
    assert c.cloud_max_calls == 7
    assert c.cloud_usd_budget == 1.25


def test_image_params_defaults(mocker):
    _env(mocker)
    c = Config()
    assert c.image_steps == 30
    assert c.image_guidance == 6.5
    assert c.image_size == 1024


def test_image_params_env_overrides(mocker):
    # The edge-image generation knobs are default_factory fields so they track
    # the env per Config(), consistent with the other tunable tiers.
    _env(mocker, CASCADE_IMAGE_STEPS="45", CASCADE_IMAGE_GUIDANCE="8.0",
         CASCADE_IMAGE_SIZE="1280")
    c = Config()
    assert c.image_steps == 45
    assert c.image_guidance == 8.0
    assert c.image_size == 1280
