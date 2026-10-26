"""Structured trace of everything the agent perceives, decides and does.

Every pipeline stage appends ``Event`` records. The CLI renders them live
(so a viewer can follow the agent's reasoning) and can dump them to JSON.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

LEVELS = {"debug": 10, "info": 20, "decision": 25, "warn": 30, "plan": 35}


@dataclass
class Event:
    seq: int
    t_ms: float                 # milliseconds since the run started
    phase: str                  # PLAN | NLP | RETRIEVE | CSP | SEARCH | RELAX | RANK | EXPLAIN | ACT
    level: str                  # debug | info | decision | warn | plan
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


class EventLog:
    def __init__(self, sink: Optional[Callable[[Event], None]] = None, min_level: str = "info"):
        self.events: List[Event] = []
        self._t0 = time.perf_counter()
        self._sink = sink
        self._min = LEVELS[min_level]

    # -- recording ---------------------------------------------------------- #
    def _emit(self, phase: str, level: str, message: str, **data: Any) -> Event:
        ev = Event(
            seq=len(self.events) + 1,
            t_ms=round((time.perf_counter() - self._t0) * 1000, 1),
            phase=phase,
            level=level,
            message=message,
            data=data,
        )
        self.events.append(ev)
        if self._sink and LEVELS[level] >= self._min:
            self._sink(ev)
        return ev

    def debug(self, phase: str, message: str, **data: Any) -> Event:
        return self._emit(phase, "debug", message, **data)

    def info(self, phase: str, message: str, **data: Any) -> Event:
        return self._emit(phase, "info", message, **data)

    def decision(self, phase: str, message: str, **data: Any) -> Event:
        """A branch point: the agent chose something and says why."""
        return self._emit(phase, "decision", message, **data)

    def warn(self, phase: str, message: str, **data: Any) -> Event:
        return self._emit(phase, "warn", message, **data)

    def plan(self, phase: str, message: str, **data: Any) -> Event:
        return self._emit(phase, "plan", message, **data)

    # -- export ------------------------------------------------------------- #
    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self.events], indent=2, default=str)

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in self.events:
            out[e.phase] = out.get(e.phase, 0) + 1
        return out
