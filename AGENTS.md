# Working on this project

Keep all real meeting material and every derivative outside the public repository.
Use only clearly fictional examples in tests, documentation, issues, and commits.
Never put real participant names, quotations, transcripts, or summaries here.

Before changing behavior, identify which stage owns it: ingestion, prompting,
validation, rendering, or execution. Keep the runtime dependency-free unless a
specific new integration warrants a dependency. Keep model adapters optional.

Every factual summary item needs source evidence; every attributed view needs
evidence from that person. Silence is not agreement. Suggestions are not adopted
plans. Exact quote checking is not semantic verification; retain that distinction.

Run `python3 -m unittest discover -s tests -v` and `python3 tools/audit_public.py`
before publishing. Review additions to `public-files.json`; never broaden it to
include generated artifacts or source folders. Inspect reachable history as well
as the current files. Use private local data only outside this checkout.
