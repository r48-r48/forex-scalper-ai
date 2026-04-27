"""Deterministic pre-trade risk checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from scalper_ai.domain import OrderIntent, OrderSide, PositionState
from scalper_ai.journal import JournalEvent, JournalEventType


class RiskDecisionStatus(str, Enum):
    """Risk decision outcome."""

    APPROVED = "approved"
    REJECTED = "rejected"


class RiskRejectCode(str, Enum):
    """Stable reject reasons emitted by the risk engine."""

    SESSION_KILL_SWITCH = "session_kill_switch"
    SYMBOL_KILL_SWITCH = "symbol_kill_switch"
    DUPLICATE_INTENT = "duplicate_intent"
    DUPLICATE_BROKER_ORDER = "duplicate_broker_order"
    REJECT_BURST = "reject_burst"
    STALE_MARKET_DATA = "stale_market_data"
    MAX_ORDER_RATE = "max_order_rate"
    MAX_DAILY_LOSS = "max_daily_loss"
    MAX_DAILY_DRAWDOWN = "max_daily_drawdown"
    MAX_POSITION = "max_position"
    REDUCE_ONLY_INCREASES_EXPOSURE = "reduce_only_increases_exposure"


@dataclass(frozen=True)
class RiskLimits:
    """Deterministic guardrails used by the pre-trade risk engine."""

    max_position_size: float
    max_daily_loss: Optional[float] = None
    max_daily_drawdown: Optional[float] = None
    max_order_rate_per_minute: int = 30
    stale_market_data_seconds: float = 2.0
    reject_burst_threshold: int = 3
    reject_burst_window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_position_size <= 0:
            raise ValueError("max_position_size must be greater than zero.")
        if self.max_daily_loss is not None and self.max_daily_loss <= 0:
            raise ValueError("max_daily_loss must be greater than zero when provided.")
        if self.max_daily_drawdown is not None and self.max_daily_drawdown <= 0:
            raise ValueError("max_daily_drawdown must be greater than zero when provided.")
        if self.max_order_rate_per_minute <= 0:
            raise ValueError("max_order_rate_per_minute must be greater than zero.")
        if self.stale_market_data_seconds <= 0:
            raise ValueError("stale_market_data_seconds must be greater than zero.")
        if self.reject_burst_threshold <= 0:
            raise ValueError("reject_burst_threshold must be greater than zero.")
        if self.reject_burst_window_seconds <= 0:
            raise ValueError("reject_burst_window_seconds must be greater than zero.")

    @classmethod
    def from_risk_config(cls, config: object) -> "RiskLimits":
        """Build limits from the application RiskConfig without coupling to config imports."""

        return cls(
            max_position_size=float(getattr(config, "max_position_size")),
            max_daily_drawdown=float(getattr(config, "max_daily_drawdown")),
            max_order_rate_per_minute=int(getattr(config, "max_order_frequency_per_minute")),
            stale_market_data_seconds=float(getattr(config, "stale_quote_seconds")),
            reject_burst_threshold=int(getattr(config, "loss_burst_threshold")),
        )


@dataclass(frozen=True)
class RiskContext:
    """Snapshot of state required for one deterministic pre-trade risk decision."""

    checked_at: datetime
    positions: Mapping[str, PositionState]
    order_timestamps: Sequence[datetime] = ()
    known_intent_ids: frozenset[str] = frozenset()
    known_broker_order_ids: frozenset[str] = frozenset()
    recent_rejection_timestamps: Sequence[datetime] = ()
    latest_market_data_at: Optional[datetime] = None
    realized_pnl_today: float = 0.0
    starting_equity: Optional[float] = None
    current_equity: Optional[float] = None
    session_kill_switch: bool = False
    symbol_kill_switches: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _ensure_aware(self.checked_at, field_name="checked_at")
        for timestamp in self.order_timestamps:
            _ensure_aware(timestamp, field_name="order_timestamps")
        for timestamp in self.recent_rejection_timestamps:
            _ensure_aware(timestamp, field_name="recent_rejection_timestamps")
        if self.latest_market_data_at is not None:
            _ensure_aware(self.latest_market_data_at, field_name="latest_market_data_at")
        if self.starting_equity is not None and self.starting_equity <= 0:
            raise ValueError("starting_equity must be greater than zero when provided.")


@dataclass(frozen=True)
class RiskDecision:
    """Result of one pre-trade risk evaluation."""

    status: RiskDecisionStatus
    checked_at: datetime
    intent_id: str
    symbol: str
    code: Optional[RiskRejectCode] = None
    reason: Optional[str] = None
    projected_position: Optional[float] = None

    @property
    def accepted(self) -> bool:
        """Return whether the order intent passed pre-trade risk checks."""

        return self.status is RiskDecisionStatus.APPROVED

    def to_journal_event(self, *, event_id: str, source: str = "risk") -> JournalEvent:
        """Render this decision as a unified journal risk event."""

        payload = {
            "status": self.status.value,
            "intent_id": self.intent_id,
            "symbol": self.symbol,
            "code": None if self.code is None else self.code.value,
            "reason": self.reason,
            "projected_position": self.projected_position,
            "checked_at": self.checked_at,
        }
        return JournalEvent.from_payload(
            event_id=event_id,
            event_type=JournalEventType.RISK,
            payload=payload,
            recorded_at=self.checked_at,
            source=source,
            correlation_id=self.intent_id,
            symbol=self.symbol,
        )


class RiskEngine:
    """Deterministic pre-trade risk engine."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    @property
    def limits(self) -> RiskLimits:
        """Return immutable risk limits."""

        return self._limits

    def evaluate_order(
        self,
        intent: OrderIntent,
        context: RiskContext,
        *,
        broker_order_id: str | None = None,
    ) -> RiskDecision:
        """Evaluate one order intent against deterministic pre-trade guardrails."""

        if context.session_kill_switch:
            return self._reject(intent, context, RiskRejectCode.SESSION_KILL_SWITCH)
        if intent.symbol in context.symbol_kill_switches:
            return self._reject(intent, context, RiskRejectCode.SYMBOL_KILL_SWITCH)
        if intent.intent_id in context.known_intent_ids:
            return self._reject(intent, context, RiskRejectCode.DUPLICATE_INTENT)
        if broker_order_id is not None and broker_order_id in context.known_broker_order_ids:
            return self._reject(intent, context, RiskRejectCode.DUPLICATE_BROKER_ORDER)
        if self._reject_burst_active(context):
            return self._reject(intent, context, RiskRejectCode.REJECT_BURST)
        if self._market_data_is_stale(context):
            return self._reject(intent, context, RiskRejectCode.STALE_MARKET_DATA)
        if self._order_rate_exceeded(context):
            return self._reject(intent, context, RiskRejectCode.MAX_ORDER_RATE)
        loss_code = self._loss_limit_code(context)
        if loss_code is not None:
            return self._reject(intent, context, loss_code)

        current_position = _current_position_for(intent.symbol, context.positions)
        projected_position = _projected_position(intent, current_position)
        if intent.reduce_only and abs(projected_position) > abs(current_position):
            return self._reject(
                intent,
                context,
                RiskRejectCode.REDUCE_ONLY_INCREASES_EXPOSURE,
                projected_position=projected_position,
            )
        if abs(projected_position) > self._limits.max_position_size:
            return self._reject(
                intent,
                context,
                RiskRejectCode.MAX_POSITION,
                projected_position=projected_position,
            )

        return RiskDecision(
            status=RiskDecisionStatus.APPROVED,
            checked_at=context.checked_at,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            projected_position=projected_position,
        )

    def _reject(
        self,
        intent: OrderIntent,
        context: RiskContext,
        code: RiskRejectCode,
        *,
        projected_position: float | None = None,
    ) -> RiskDecision:
        return RiskDecision(
            status=RiskDecisionStatus.REJECTED,
            checked_at=context.checked_at,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            code=code,
            reason=code.value,
            projected_position=projected_position,
        )

    def _reject_burst_active(self, context: RiskContext) -> bool:
        window_start = context.checked_at - timedelta(
            seconds=self._limits.reject_burst_window_seconds
        )
        recent_count = sum(
            1 for timestamp in context.recent_rejection_timestamps if timestamp >= window_start
        )
        return recent_count >= self._limits.reject_burst_threshold

    def _market_data_is_stale(self, context: RiskContext) -> bool:
        if context.latest_market_data_at is None:
            return True
        age = context.checked_at - context.latest_market_data_at
        return age.total_seconds() > self._limits.stale_market_data_seconds

    def _order_rate_exceeded(self, context: RiskContext) -> bool:
        window_start = context.checked_at - timedelta(seconds=60)
        recent_count = sum(1 for timestamp in context.order_timestamps if timestamp >= window_start)
        return recent_count >= self._limits.max_order_rate_per_minute

    def _loss_limit_code(self, context: RiskContext) -> RiskRejectCode | None:
        if (
            self._limits.max_daily_loss is not None
            and context.realized_pnl_today <= -self._limits.max_daily_loss
        ):
            return RiskRejectCode.MAX_DAILY_LOSS
        if (
            self._limits.max_daily_drawdown is not None
            and context.starting_equity is not None
            and context.current_equity is not None
        ):
            drawdown = (context.starting_equity - context.current_equity) / context.starting_equity
            if drawdown >= self._limits.max_daily_drawdown:
                return RiskRejectCode.MAX_DAILY_DRAWDOWN
        return None


def _projected_position(intent: OrderIntent, current_position: float) -> float:
    if intent.target_position is not None:
        return float(intent.target_position)
    if intent.quantity is None:
        raise ValueError("OrderIntent must contain quantity or target_position.")
    raw_quantity = float(intent.quantity)
    signed_quantity = raw_quantity if intent.side is OrderSide.BUY else -raw_quantity
    return current_position + signed_quantity


def _current_position_for(symbol: str, positions: Mapping[str, PositionState]) -> float:
    position = positions.get(symbol)
    return 0.0 if position is None else float(position.net_quantity)


def _ensure_aware(timestamp: datetime, *, field_name: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
