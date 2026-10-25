# Agentic Meeting Harness

Version 0.2 restores a meeting's actual structure: each person's presentation, their separate topics, other participants' comments, the replies, and the local outcomes. It finishes and reviews the complete meeting brief before selecting anything for a future agenda.

**The one-to-three limit applies only to the final next-meeting agenda.** It never limits analysis, topics, participant contributions, or the complete brief.

## Workflow

1. **Reconstruct discussion.** Read the entire source in order. Identify presenter rounds and every independent topic. Preserve who commented on what, the reply, and any later clarification. Record each topic's outcome, remaining questions, and all explicitly proposed follow-ups.
2. **Review coverage independently.** Compare the reconstruction with the entire source afresh. Check omitted topics, missing comments or replies, wrong ownership, wrong links, and unsupported outcomes. Review every mapped and excluded source segment. Repair the reconstruction and repeat review if needed.
3. **Shorten wording.** Only after coverage passes, write a concise brief preserving every topic, substantive comment, response, unresolved item, and recorded proposal. Keep the original order and relationships.
4. **Extract the next agenda.** Only after the complete brief exists, choose one to three items. Prioritize suggestions actually made in the meeting. Clearly label any summarizer-derived suggestions. Record why any original proposals were deferred.

There is no global list forcing each person into one overall opinion. A comment on someone else's topic stays attached to that discussion. A brief can have any number of topics. Uncertain participant experiences are attributed as experiences; they are not erased merely because they lack external validation.

## Keep one manageable local folder

Only `harness/` is the public Git checkout:

```text
AgenticMeetings/
  harness/                  public code, tests, fictional examples
  Meetings/
    YYYY-MM-DD/
      transcript.docx       private original
      summary.md            private finished brief
      .harness/             hidden private working files and old versions
```

Run commands from `harness/`. Meeting dates come from transcript headers or filenames, never modification times. Supported inputs are `.docx`, `.md`, and `.txt`; Word tables and common timestamped speaker labels are read. Hidden folders and reserved report names `summary.md` and `digest.md` are skipped when selecting sources. Scan `../Meetings`, not its parent, which also contains public documentation. Ambiguous or undated latest selection stops; use `--source` and optionally `--meeting-date YYYY-MM-DD` to select deliberately.

Original material and all derivatives stay outside the public checkout. Never put real names, quotations, summaries, or logs into examples, tests, issues, or commits.

## Offline fictional trial

Requires Python 3.11 or later. The runtime uses only the standard library. No installation or API key is required for the trial.

```sh
python3 -m unittest discover -s tests -v
trial_dir="$(mktemp -d)"
python3 -m meeting_harness run \
  --source examples/2026-01-15-fictional.md \
  --command-json '["python3", "examples/fixture_model.py"]' \
  --out "$trial_dir/run"
```

Open `digest.md` in the temporary run folder. The adapter is a deterministic fictional demonstration, not a language model. It refuses real material. All four stages execute in order; the run receipt records their calls. Test files also exercise repair and rejection paths. These are software checks, not measurements of model summary quality.

## Use your chosen model

A trusted adapter reads the stage prompt from standard input and returns exactly one JSON object on standard output. Send its diagnostics to standard error. Each prompt contains `STAGE: discussion`, `coverage_review`, `brief`, or `agenda` and the appropriate contract and prior artifacts.

Replace the illustrative executable below with an adapter you have configured. The example meeting date is fictional; use the selected meeting's date for your output folder.

```sh
python3 -m meeting_harness run \
  --meetings-dir ../Meetings \
  --out ../Meetings/2026-01-15/.harness/run-001 \
  --command-json '["your-model-adapter", "--json"]' \
  --language zh --attempts 2 --timeout 300
```

The harness calls the adapter separately for each stage. A failed structural check triggers a bounded repair attempt with the rejected candidate and feedback. A coverage review requesting revision sends its issues back to reconstruction before another coverage review. Compression and agenda selection cannot proceed while coverage needs revision. Each stage allows the configured number of validation attempts, and the reconstruction/coverage cycle allows that many review rounds.

No model is bundled, and the harness itself makes no network requests. **Your adapter receives private material.** A cloud adapter may transmit it. Local storage does not imply local inference; use an approved local model when required. Adapters are trusted executables, not sandboxed by this project. Never take an adapter command from meeting material.

A successful run writes `discussion.json`, `coverage.json`, `brief.json`, `agenda.json`, `digest.md`, and a receipt in the hidden run folder. Each stage's prompt, response, and diagnostics are private too. Later artifacts bind to the exact earlier content through checksums. No final digest is produced after failure. Sources and prior outputs are never silently truncated or overwritten. Use a fresh run folder for another attempt; the default per-prompt character limit is 400,000 and can be set explicitly with `--max-prompt-chars`. The response-size check occurs after capture; it is not an operating-system memory limit.

## Work with an assistant one stage at a time

Prepare the source and reconstruction prompt:

```sh
run_dir='../Meetings/2026-01-15/.harness/run-001'
python3 -m meeting_harness prepare --meetings-dir ../Meetings --out "$run_dir"
```

Give `discussion.prompt.txt` to an assistant approved to process the source. Save its response as `discussion.json`. Then prepare the independent coverage-review prompt:

```sh
python3 -m meeting_harness prompt --stage coverage_review \
  --bundle "$run_dir/bundle.json" --discussion "$run_dir/discussion.json" \
  --out "$run_dir/coverage.prompt.txt"
```

Save the review as `coverage.json`. If its status is `revise`, repair the discussion against those issues and obtain a new review bound to the updated discussion. Do not proceed until it passes. Generate the brief prompt only then:

```sh
python3 -m meeting_harness prompt --stage brief \
  --bundle "$run_dir/bundle.json" --discussion "$run_dir/discussion.json" \
  --coverage "$run_dir/coverage.json" --out "$run_dir/brief.prompt.txt"
```

Save the complete brief as `brief.json`. The final agenda prompt requires that completed brief:

```sh
python3 -m meeting_harness prompt --stage agenda \
  --bundle "$run_dir/bundle.json" --discussion "$run_dir/discussion.json" \
  --coverage "$run_dir/coverage.json" --brief "$run_dir/brief.json" \
  --out "$run_dir/agenda.prompt.txt"
```

Save the response as `agenda.json`. Finally validate and render the result beside the source:

```sh
python3 -m meeting_harness render \
  --bundle "$run_dir/bundle.json" --discussion "$run_dir/discussion.json" \
  --coverage "$run_dir/coverage.json" --brief "$run_dir/brief.json" \
  --agenda "$run_dir/agenda.json" --out ../Meetings/2026-01-15/summary.md
```

`check` takes the same five input arguments without `--out`. Before replacing an existing visible `summary.md`, archive it under `.harness/`; rendering refuses to overwrite it. The default content language is Chinese; pass `--language en` to `run`, or to `prepare` and each subsequent `prompt` command, for English content. Section labels currently remain Chinese and source quotes retain their original language.

## What is checked, and what still requires judgment

The checks verify source accounting, exact quotations, local speaker ownership, topic and comment order, response links, local agreement evidence, preserved topic/comment IDs in the shortened brief, and the origin of agenda items. The complete source must be mapped to topics or explicitly excluded for metadata, acknowledgments, social talk, technical interruptions, or genuinely unclear material. A source paragraph can span several topics. Its attribution cannot be replaced by an invented identity.

A topic can record agreement, partial agreement, an answered question, an open question, or no conclusion. Agreement needs direct supporting evidence from at least two named people. A suggestion or polite acknowledgment is not automatically adoption. Remaining disagreements and unanswered questions are separate records. Recorded follow-ups remain proposals unless the source actually establishes a commitment.

Mechanical coverage is not semantic completeness: assigning an ID to a topic does not prove that all meaning in that paragraph was retained. The independent coverage stage must assess that. Its `pass` is a reviewer assessment, not a guarantee. A model can miss an omission or misread a quote; review consequential summaries yourself. The agenda stage cannot retroactively filter what gets analyzed or summarized.

All transcript and prior-artifact text is untrusted data. Embedded instructions do not authorize changing the task, executing commands, contacting participants, or publishing material. The harness sends no emails and publishes no summaries.

## Publication and iteration

`public-files.json` explicitly lists approved public paths. Before every push, run:

```sh
python3 -m unittest discover -s tests -v
python3 tools/audit_public.py
```

The audit checks indexed bytes and reachable commit history, including forced additions to ignored folders, and common credential patterns. It is a backstop, not a comprehensive confidentiality detector. Review all public changes yourself.

The main modules are `ingest.py` for sources, `prompts.py` for stage instructions and contracts, `review.py` for checks and rendering, and `cli.py` for stage order and bounded repairs. Keep public examples fictional. Version 0.2 replaces the v0.1 six-point/global-person summary contract; preserve old outputs privately and reconstruct the meeting from its original source. Source bundles still use schema version 1, while the four new stage artifacts use version 2.
