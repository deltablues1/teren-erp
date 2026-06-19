"""Mali in-memory registar pozadinskih poslova (uvoz troškovnika).

Uvoz troškovnika zna trajati nekoliko minuta (više Claude poziva po chunku).
Ako to teče sinkrono u HTTP zahtjevu, browser/proxy timeout prekine zahtjev
prije nego se išta upiše u bazu — projekt ostane prazan ("ključ u ruke").

Zato uvoz teče u zasebnoj dretvi, a ovaj registar drži status koji panel
povlači (poll) preko /status endpointa. Stanje je samo u memoriji procesa —
to je u redu jer i web panel i bot vrte kao jedan dugotrajan proces; restart
procesa pobriše povijest poslova, što je prihvatljivo (uvoz se ponovi).
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    id: str
    projekt_key: str
    naziv_datoteke: str
    status: str = "running"          # running | done | error
    done: int = 0                    # obrađeni chunkovi
    total: int = 0                   # ukupno chunkova (0 dok se ne izračuna)
    n: int = 0                       # upisanih stavki (po završetku)
    greska: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def postotak(self) -> int:
        if self.status == "done":
            return 100
        if self.total <= 0:
            return 0
        return min(99, int(self.done * 100 / self.total))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "postotak": self.postotak,
            "n": self.n,
            "greska": self.greska,
        }


_lock = threading.Lock()
_jobs: dict[str, Job] = {}
_MAX_AGE = 3600  # zadrži završene poslove sat vremena


def _gc() -> None:
    """Počisti stare završene poslove (drži registar malim)."""
    cutoff = time.time() - _MAX_AGE
    for jid in [j.id for j in _jobs.values()
                if j.status != "running" and j.updated < cutoff]:
        _jobs.pop(jid, None)


def create(projekt_key: str, naziv_datoteke: str) -> Job:
    with _lock:
        _gc()
        job = Job(id=uuid.uuid4().hex[:12], projekt_key=projekt_key,
                  naziv_datoteke=naziv_datoteke)
        _jobs[job.id] = job
        return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def set_progress(job_id: str, done: int, total: int) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.done, job.total, job.updated = done, total, time.time()


def finish(job_id: str, n: int) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.status, job.n, job.updated = "done", n, time.time()


def fail(job_id: str, greska: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.status, job.greska, job.updated = "error", greska, time.time()
