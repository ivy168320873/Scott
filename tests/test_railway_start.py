import railway_start


def test_runtime_env_enables_worker_and_forces_paper_trading():
    env = railway_start.runtime_env(
        {
            "PORT": "8080",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "ALPACA_PAPER": "false",
            "ENABLE_LIVE_TRADING": "stale-confirmation",
        }
    )

    assert env["MARKET_INTELLIGENCE_ENABLE"] == "true"
    assert env["ALPACA_PAPER"] == "true"
    assert "ENABLE_LIVE_TRADING" not in env
    assert railway_start.gunicorn_command(env)[-2:] == ["0.0.0.0:8080", "app:app"]


def test_worker_can_be_explicitly_disabled():
    original = {
        "RAILWAY_INTELLIGENCE_WORKER_ENABLE": "false",
        "MARKET_INTELLIGENCE_ENABLE": "false",
    }

    assert railway_start.worker_enabled(original) is False
    assert railway_start.runtime_env(original)["MARKET_INTELLIGENCE_ENABLE"] == "false"


def test_worker_does_not_auto_start_without_a_persistent_volume():
    assert railway_start.worker_enabled({}) is False
    assert railway_start.worker_enabled({"RAILWAY_VOLUME_MOUNT_PATH": "/data"}) is True


def test_intelligence_command_is_shell_free():
    assert railway_start.intelligence_command() == [
        railway_start.sys.executable,
        "-m",
        "market_intelligence.worker",
        "daemon",
    ]
