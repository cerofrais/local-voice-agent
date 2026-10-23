"""Resolve "backwaters", "the Munnar trip", "Phuket" to a destination code the CRM accepts."""

import re
from dataclasses import dataclass

from agent.crm.models import Destination


@dataclass
class Candidate:
    destination: Destination
    score: float
    matched: str


class DestinationResolver:
    def __init__(self, destinations: list[Destination]):
        self.destinations = destinations
        self._by_code = {d.code: d for d in destinations}

    def by_code(self, code: str) -> Destination | None:
        return self._by_code.get(code)

    def names(self) -> list[str]:
        return [d.name for d in self.destinations]

    def resolve(self, text: str) -> list[Candidate]:
        """Candidates best first. A whole-word alias match scores by how specific the alias is."""
        t = " " + re.sub(r"[^a-z0-9' ]", " ", text.lower()) + " "
        t = re.sub(r"\s+", " ", t)
        out: dict[str, Candidate] = {}
        for d in self.destinations:
            terms = {d.code, d.name.lower(), *[a.lower() for a in d.aliases]}
            for term in terms:
                needle = " " + re.sub(r"[^a-z0-9' ]", " ", term).strip() + " "
                if needle.strip() and needle in t:
                    # exact destination name/code beats an area/city alias; longer beats shorter
                    base = 1.0 if term in (d.code, d.name.lower()) else 0.8
                    score = base + min(len(term), 20) / 100
                    cur = out.get(d.code)
                    if cur is None or score > cur.score:
                        out[d.code] = Candidate(d, round(score, 3), term)
        ranked = sorted(out.values(), key=lambda c: -c.score)
        return ranked
