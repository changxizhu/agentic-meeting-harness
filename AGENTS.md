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
based on the future agenda. After the full brief, write a genuinely condensed
summary of every topic. Only after both summaries are complete, select one to
three next-discussion items, prioritizing recorded proposals and identifying
extensions.

Every completed meeting must produce both reports together. Name the concise
report `YYYY-MM-DD <report title>.md` and the detailed edition
`YYYY-MM-DD <report title> [with references].md`. Use the verified meeting date,
never today's date. Keep group-specific report titles in the ignored local
Meetings/.harness/report-naming.json configuration, not public code or examples.
The concise report has no reference markers or source appendix; the detailed
edition includes evidence and quotations.
Both use the same reviewed discussion and final agenda. The reference edition
keeps the detailed brief and evidence. The concise edition must rewrite the
content, not merely remove references or tighten the layout: retain every topic
in order and use a few sentences per topic (normally three to five in total) to
describe the share, the main attributed comments and replies, and the result.
Merge related comments, remove repetition and minor exchanges, and retain the
meaning of unresolved disagreements, open questions, uncertainty, and narrow
local agreement. Do not replay every comment, position, or follow-up proposal.
Never shorten by deleting entire topics or cutting text mechanically.

Run the stages in this order: discussion, coverage_review, brief, concise, agenda.
The separate concise artifact is mandatory and must cover all approved topics;
never fall back to rendering the full brief without citations. Check its meaning
against the reviewed discussion before writing the final agenda. One to three
still limits only the final next-discussion agenda, not meeting topic coverage.

The run command writes both dated reports into its output folder. Prefer a
directory for render --out to use the same naming rule. A legacy explicit
Markdown output path still names the concise report; its companion adds
` [with references]` before `.md`. Check both destinations before writing either
report. Never overwrite an existing file; privately archive both reports before
regenerating at the same paths. Exclude generated reports from automatic
meeting-source selection.

Run `python3 -m unittest discover -s tests -v` and `python3 tools/audit_public.py`
before publishing. Review additions to `public-files.json`; never broaden it to
include generated artifacts or source folders. Inspect reachable history as well
as the current files. Local Meetings/ content remains excluded even when a forced
addition bypasses ignore rules: the publication audit must reject it.
