"""Deterministic fictional demo adapter. This is NOT a language model.

Only accepts the checked-in fictional fixture; never use it on real meetings.
The fixture deliberately has shared agreement and an unresolved disagreement.
"""

import hashlib
import json
from pathlib import Path
import sys


def main():
    prompt = sys.stdin.read()
    fixture = Path(__file__).with_name("2026-01-15-fictional.md")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    if digest not in prompt:
        print("This demonstration adapter only supports the fictional fixture.", file=sys.stderr)
        return 2

    def ev(segment_id, quote):
        return [{"segment_id": segment_id, "quote": quote}]

    a = ev("S0003", "We should hold a small workshop before planning a large event.")
    b = ev("S0004", "I agree with a small workshop, but I prefer an evening session.")
    c = ev("S0005", "I prefer a weekend session because volunteers have different schedules.")
    d = ev("S0006", "We have not chosen a time. Let us compare volunteer availability next time.")
    summary = {
        "schema_version": 1,
        "meeting": {"title": "Fictional community project discussion", "date": "2026-01-15", "source_sha256": digest},
        "core_points": [{"id": "C1", "point": "Start with a small workshop; its timing remains undecided.", "evidence": a + d}],
        "participant_views": [
            {"speaker": "Alex", "view": "Start small and compare availability before choosing a time.", "evidence": a + d},
            {"speaker": "Blair", "view": "Supports a small workshop and prefers an evening session.", "evidence": b},
            {"speaker": "Casey", "view": "Prefers a weekend session to accommodate volunteers.", "evidence": c}],
        "consensus": {"status": "partial", "summary": "Alex and Blair support a small workshop; Casey's position on its size is not recorded.",
                      "participants": ["Alex", "Blair"], "evidence": a + b},
        "unresolved": [{"issue": "Evening or weekend timing remains undecided.", "status": "disagreement", "evidence": b + c + d,
                        "positions": [{"speaker": "Blair", "view": "Evening session.", "evidence": b},
                                      {"speaker": "Casey", "view": "Weekend session.", "evidence": c}]}],
        "next_discussion": [{"question": "Which session time best fits volunteer availability?", "why": "Resolve the scheduling disagreement with availability data.",
                             "based_on": ["C1", "U1"], "kind": "proposal"}],
        "limitations": ["This is a fictional demonstration, not an actual meeting."]}
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
