"""Tests for MT5 operational preflight helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scalper_ai.config import load_app_config
from scalper_ai.deployment.live_factory import build_mt5_terminal_client
from scalper_ai.deployment.mt5_preflight import build_mt5_preflight_report
from scalper_ai.utils.paths import resolve_repo_root


def test_build_mt5_preflight_report_surfaces_missing_package_and_path(
    tmp_path: Path,
) -> None:
    config = load_app_config(config_name="mt5", config_dir=resolve_repo_root() / "configs")
    executable = (
        tmp_path
        / "Applications"
        / "MetaTrader 5.app"
        / "Wrapper"
        / "MetaTrader5Terminal.app"
        / "MetaTrader5Terminal"
    )
    executable.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    report = build_mt5_preflight_report(
        config,
        search_roots=(tmp_path / "Applications",),
        module_loader=_raise_missing_package,
    )

    assert report.package_installed is False
    assert report.discovered_terminal_path == executable.resolve()
    assert report.resolved_terminal_path == executable.resolve()
    assert report.ready_for_connection is False
    assert any("MetaTrader5 Python package is not installed" in error for error in report.errors)
    assert any("Auto-discovered MT5 terminal executable" in note for note in report.notes)
    assert any("account_mode='hedging'" in note for note in report.notes)


def test_build_mt5_preflight_report_rejects_invalid_configured_terminal_path(
    tmp_path: Path,
) -> None:
    missing_terminal = tmp_path / "missing-terminal"
    base_config = load_app_config(config_name="mt5", config_dir=resolve_repo_root() / "configs")
    broker_config = base_config.broker.model_copy(
        update={
            "mt5": base_config.broker.mt5.model_copy(
                update={"terminal_path": missing_terminal}
            )
        }
    )
    config = base_config.model_copy(update={"broker": broker_config})

    report = build_mt5_preflight_report(
        config,
        module_loader=lambda: object(),
    )

    assert report.package_installed is True
    assert report.ready_for_connection is False
    assert any("does not resolve to a file" in error for error in report.errors)


def test_build_mt5_terminal_client_uses_discovered_terminal_path(monkeypatch) -> None:
    config = load_app_config(config_name="mt5", config_dir=resolve_repo_root() / "configs")
    fake_module = _FactoryFakeMetaTrader5Module()
    monkeypatch.setattr(
        "scalper_ai.deployment.live_factory.discover_mt5_terminal_path",
        lambda configured_path: Path("/tmp/MetaTrader5Terminal"),
    )

    client = build_mt5_terminal_client(config, mt5_module=fake_module)

    assert fake_module.initialize_kwargs["path"] == "/tmp/MetaTrader5Terminal"
    client.close()
    assert fake_module.shutdown_called is True


def _raise_missing_package() -> object:
    raise RuntimeError("missing package")


class _FactoryFakeMetaTrader5Module:
    def __init__(self) -> None:
        self.initialize_kwargs: dict[str, object] = {}
        self.shutdown_called = False

    def initialize(self, **kwargs: object) -> bool:
        self.initialize_kwargs = dict(kwargs)
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(name="MT5")

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=1,
            server="demo",
            balance=1000.0,
            equity=1000.0,
            leverage=100,
        )
