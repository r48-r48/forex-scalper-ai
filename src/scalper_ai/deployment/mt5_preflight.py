"""Operational helpers for validating MT5 runtime prerequisites before live connection attempts."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from scalper_ai.config import AppConfig
from scalper_ai.execution.mt5_client import discover_mt5_terminal_path, is_metatrader5_package_available

LIVE_CONFIRMATION_ENV_VAR = "SCALPER_AI_LIVE_CONFIRMATION"


@dataclass(frozen=True)
class Mt5PreflightReport:
    """Structured MT5 readiness snapshot for scripts, diagnostics, and support flows."""

    adapter_name: str
    environment: str
    package_installed: bool
    configured_terminal_path: Path | None
    discovered_terminal_path: Path | None
    resolved_terminal_path: Path | None
    login_configured: bool
    password_configured: bool
    server_configured: bool
    live_confirmation_present: bool
    account_mode: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ready_for_connection(self) -> bool:
        """Return whether the local environment is ready for an MT5 connection attempt."""

        return not self.errors and self.package_installed

    def to_dict(self) -> dict[str, object]:
        """Serialize the report into JSON-friendly primitives."""

        return {
            "adapter_name": self.adapter_name,
            "environment": self.environment,
            "package_installed": self.package_installed,
            "configured_terminal_path": None
            if self.configured_terminal_path is None
            else str(self.configured_terminal_path),
            "discovered_terminal_path": None
            if self.discovered_terminal_path is None
            else str(self.discovered_terminal_path),
            "resolved_terminal_path": None if self.resolved_terminal_path is None else str(self.resolved_terminal_path),
            "login_configured": self.login_configured,
            "password_configured": self.password_configured,
            "server_configured": self.server_configured,
            "live_confirmation_present": self.live_confirmation_present,
            "account_mode": self.account_mode,
            "ready_for_connection": self.ready_for_connection,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


def build_mt5_preflight_report(
    config: AppConfig,
    *,
    env: Mapping[str, str] | None = None,
    search_roots: Sequence[Path] | None = None,
    module_loader: Any = None,
) -> Mt5PreflightReport:
    """Build one MT5 operational readiness report from typed application config."""

    env_map = os.environ if env is None else env
    mt5_config = config.broker.mt5
    configured_terminal_path = None if mt5_config.terminal_path is None else mt5_config.terminal_path.expanduser()
    discovered_terminal_path = discover_mt5_terminal_path(search_roots=search_roots)
    resolved_terminal_path = discover_mt5_terminal_path(configured_terminal_path, search_roots=search_roots)
    package_installed = is_metatrader5_package_available(module_loader=module_loader)

    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    adapter_name = config.broker.live_adapter.strip().lower()
    if adapter_name != "mt5":
        warnings.append(f"Selected live adapter is '{config.broker.live_adapter}', not 'mt5'.")
    if mt5_config.account_mode != "netting":
        errors.append("MT5 live integration currently supports only account_mode='netting'.")

    if configured_terminal_path is not None and resolved_terminal_path is None:
        errors.append(f"Configured MT5 terminal_path does not resolve to a file: {configured_terminal_path}")
        if discovered_terminal_path is not None:
            notes.append(
                f"An auto-discovered MT5 terminal executable is available at {discovered_terminal_path}, but the explicit terminal_path takes precedence."
            )
    elif configured_terminal_path is None and resolved_terminal_path is None:
        warnings.append(
            "No MT5 terminal executable was configured or auto-discovered. MetaTrader5 will rely on its default search path."
        )
    elif configured_terminal_path is None and resolved_terminal_path is not None:
        notes.append(f"Auto-discovered MT5 terminal executable at {resolved_terminal_path}.")

    if not package_installed:
        errors.append("MetaTrader5 Python package is not installed in this environment.")
    if mt5_config.login is None:
        warnings.append("MT5 login is not configured. Initialization will rely on an already authorized terminal session.")
    if mt5_config.password is None:
        warnings.append("MT5 password is not configured. Initialization will rely on terminal-side saved credentials.")
    if mt5_config.server is None:
        warnings.append("MT5 server is not configured. Initialization will rely on terminal-side saved broker server state.")

    live_confirmation = env_map.get(LIVE_CONFIRMATION_ENV_VAR, "").strip()
    if config.deployment.require_live_confirmation and not live_confirmation:
        warnings.append(
            "SCALPER_AI_LIVE_CONFIRMATION is not set. Runtime bootstrap will refuse true live mode until it is provided."
        )

    return Mt5PreflightReport(
        adapter_name=config.broker.live_adapter,
        environment=config.environment,
        package_installed=package_installed,
        configured_terminal_path=configured_terminal_path,
        discovered_terminal_path=discovered_terminal_path,
        resolved_terminal_path=resolved_terminal_path,
        login_configured=mt5_config.login is not None,
        password_configured=mt5_config.password is not None,
        server_configured=mt5_config.server is not None,
        live_confirmation_present=bool(live_confirmation),
        account_mode=mt5_config.account_mode,
        errors=tuple(errors),
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
