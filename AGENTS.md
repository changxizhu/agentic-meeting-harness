# Working on this project

Keep all real meeting material and every derivative in the ignored local Meetings/
folder (or outside this checkout). Group each original, finished summary, and hidden
working records under Meetings/YYYY-MM-DD/. Never stage or publish this folder.
Use only clearly fictional examples in public tests, documentation, issues, and
commits. Real participant names, quotations, transcripts, and summaries must never
enter public files or Git history.

Before changing behavior, identify which stage owns it: ingestion, prompting,
validation, rendering, or execution. Keep the runtime dependency-free unless a
specific new integration warrants a dependency. Keep model adapters optional.

Every factual summary item needs source evidence; every attributed view needs
evidence from that person. Silence is not agreement. Suggestions are not adopted
plans. Exact quote checking is not semantic verification; retain that distinction.

Restore all presenter-led topics and comment-to-response relationships before
shortening wording. An independent full-source coverage review must pass before
the complete brief is written. Do not impose a topic count or choose what to omit
based on the future agenda. Only after the brief is complete, select one to three
next-discussion items, prioritizing recorded proposals and identifying extensions.

Every completed meeting must produce both reports together: summary.md is the
concise report without reference markers or a source appendix;
summary_with_references.md is the detailed report with evidence and quotations.
Both use the same reviewed discussion, brief, and final agenda. Preserve every
topic in order, essential comments and replies, individual attribution, outcomes,
and unresolved questions in both versions. Shorten wording and layout; never
reduce topic coverage to make the concise version shorter. Keep the four model
stages unchanged. One to three still limits only the final next-discussion agenda.

The run command writes both reports into its output folder. The render command's
output path names the concise report and adds a sibling with _with_references
before .md. Check both destinations before writing either report. Never overwrite
an existing file; privately archive both reports before regenerating at the same
paths. Exclude generated reports from automatic meeting-source selection.

Run `python3 -m unittest discover -s tests -v` and `python3 tools/audit_public.py`
before publishing. Review additions to `public-files.json`; never broaden it to
include generated artifacts or source folders. Inspect reachable history as well
as the current files. Local Meetings/ content remains excluded even when a forced
addition bypasses ignore rules: the publication audit must reject it.
