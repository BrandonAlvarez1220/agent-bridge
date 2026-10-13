"""Reloj inyectable en UTC.

Toda la lógica de vencimientos (leases de tareas, reservas) pide la hora a un
``Clock`` en lugar de llamar a ``datetime.now`` directamente. Así las pruebas
pueden adelantar el tiempo sin dormir (ver ADR-0007).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Clock = Callable[[], datetime]

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def system_clock() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Serializa a texto de ancho fijo para que la comparación lexicográfica sea cronológica."""
    if moment.tzinfo is None:
        raise ValueError("se requiere un datetime con zona horaria")
    return moment.astimezone(UTC).strftime(_ISO_FORMAT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _ISO_FORMAT).replace(tzinfo=UTC)


class FakeClock:
    """Reloj controlable para pruebas."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
