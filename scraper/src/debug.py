"""
SyntaX Speed Debugger
Color-coded timing breakdown for every phase of the request pipeline.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional


# ANSI colors
class C:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    WHITE = "\033[97m"
    MAGENTA = "\033[95m"


def _color_ms(ms: float) -> str:
    """Color-code a millisecond value: green <100, yellow 100-300, red >300."""
    if ms < 100:
        return f"{C.GREEN}{ms:>7.1f}ms{C.RESET}"
    elif ms < 300:
        return f"{C.YELLOW}{ms:>7.1f}ms{C.RESET}"
    else:
        return f"{C.RED}{ms:>7.1f}ms{C.RESET}"


def _bar(ms: float, max_ms: float = 500) -> str:
    """Create a visual bar showing relative time."""
    width = min(int(ms / max_ms * 30), 30)
    if ms < 100:
        color = C.GREEN
    elif ms < 300:
        color = C.YELLOW
    else:
        color = C.RED
    return f"{color}{'█' * width}{'░' * (30 - width)}{C.RESET}"


@dataclass
class TimingPhase:
    name: str
    start: float = 0
    end: float = 0

    @property
    def ms(self) -> float:
        return (self.end - self.start) * 1000


@dataclass
class RequestDebug:
    """Collects timing data for a single request."""
    label: str = ""
    phases: List[TimingPhase] = field(default_factory=list)
    _current: Optional[TimingPhase] = field(default=None, repr=False)
    status_code: int = 0
    response_size: int = 0

    def phase(self, name: str) -> "RequestDebug":
        """Start a new timing phase."""
        if self._current:
            self._current.end = time.perf_counter()
        p = TimingPhase(name=name, start=time.perf_counter())
        self.phases.append(p)
        self._current = p
        return self

    def end(self):
        """End the current phase."""
        if self._current:
            self._current.end = time.perf_counter()
            self._current = None

    @property
    def total_ms(self) -> float:
        if not self.phases:
            return 0
        return (self.phases[-1].end - self.phases[0].start) * 1000

    def print(self):
        """Print the timing breakdown."""
        self.end()
        total = self.total_ms

        print(f"\n  {C.BOLD}{C.CYAN}⚡ {self.label}{C.RESET}", end="")
        if self.status_code:
            sc = self.status_code
            color = C.GREEN if sc == 200 else C.RED
            print(f"  {color}[{sc}]{C.RESET}", end="")
        print(f"  {_color_ms(total)}")

        print(f"  {'─' * 55}")

        for p in self.phases:
            ms = p.ms
            print(f"  {p.name:<20} {_color_ms(ms)}  {_bar(ms)}")

        print(f"  {'─' * 55}")
        print(f"  {C.BOLD}{'TOTAL':<20}{C.RESET} {_color_ms(total)}  {_bar(total, 1000)}")

        if self.response_size:
            print(f"  {C.DIM}Response: {self.response_size:,} bytes{C.RESET}")


class SpeedDebugger:
    """
    Collects and displays timing data across multiple requests.
    Shows cold vs warm performance and overall statistics.
    """

    def __init__(self):
        self.requests: List[RequestDebug] = []
        self._init_debug: Optional[RequestDebug] = None

    def new_request(self, label: str) -> RequestDebug:
        """Create a new request debug tracker."""
        rd = RequestDebug(label=label)
        self.requests.append(rd)
        return rd

    def set_init_debug(self, rd: RequestDebug):
        """Set the initialization timing (txn generator, session warmup)."""
        self._init_debug = rd

    def print_summary(self):
        """Print overall speed summary."""
        print(f"\n{'═' * 60}")
        print(f"{C.BOLD}{C.CYAN}  SyntaX Speed Report{C.RESET}")
        print(f"{'═' * 60}")

        if self._init_debug:
            self._init_debug.print()

        for rd in self.requests:
            if rd is not self._init_debug:
                rd.print()

        # Stats (exclude init from request stats)
        api_requests = [r for r in self.requests if r is not self._init_debug]
        if api_requests:
            times = [r.total_ms for r in api_requests if r.total_ms > 0]
            if times:
                avg = sum(times) / len(times)
                fastest = min(times)
                slowest = max(times)

                print(f"\n  {C.BOLD}Stats ({len(times)} requests){C.RESET}")
                print(f"  {'─' * 55}")
                print(f"  {'Fastest':<20} {_color_ms(fastest)}")
                print(f"  {'Slowest':<20} {_color_ms(slowest)}")
                print(f"  {'Average':<20} {_color_ms(avg)}")

                if len(times) >= 2:
                    cold = times[0]
                    warm_avg = sum(times[1:]) / len(times[1:])
                    print(f"\n  {C.BOLD}Cold vs Warm{C.RESET}")
                    print(f"  {'─' * 55}")
                    print(f"  {'Cold (1st req)':<20} {_color_ms(cold)}")
                    print(f"  {'Warm (avg)':<20} {_color_ms(warm_avg)}")
                    speedup = cold / warm_avg if warm_avg > 0 else 0
                    print(f"  {'Speedup':<20} {C.GREEN}{speedup:.1f}x{C.RESET}")

        print(f"\n{'═' * 60}")
