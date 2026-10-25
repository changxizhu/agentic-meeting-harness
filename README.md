# Agentic Meeting Harness

A small, local workflow for turning recurring meeting transcripts into concise, evidence-backed briefs. Version 0.1 is intended to be easy to inspect and change.

The brief answers five questions:

1. What were the core discussion points?
2. What did each person say, with supporting source excerpts?
3. What agreement was explicitly recorded?
4. What disagreements or questions remain unresolved?
5. Which one to three topics are worth discussing next?

Individual views appear as supporting evidence. Follow-up topics are labelled proposals, never decisions made by the meeting.

## Quick start

Requires Python 3.11 or later. The runtime uses only the Python standard library. Run from this project directory; installation and an API key are unnecessary for the demo.

```sh
python3 -m unittest discover -s tests -v

demo_dir="$(mktemp -d)"
python3 -m meeting_harness run \
  --source examples/2026-01-15-fictional.md \
  --command-json '["python3", "examples/fixture_model.py"]' \
  --out "$demo_dir/run"
```

Open `digest.md` in the temporary run folder. This keeps demonstration output out of your project folders. The checked-in adapter is a deterministic **fictional demonstration**, not a language model. It rejects other sources. Choose a fresh output directory for every run; the harness does not overwrite previous runs.

## Use real meeting material privately

Keep everything under one parent folder. Only `harness/` is the public Git checkout; each meeting's original and finished summary stay together in its dated folder:

```text
workspace/
  AgenticMeetings/
    harness/             public code and fictional examples
    Meetings/
      2026-01-15/
        transcript.docx  private original
        summary.md       private finished brief
        .harness/        hidden private working files
```

Run the commands below from `harness/`. Prepare the latest meeting, using the selected meeting's date for its working folder (the example date is fictional):

```sh
python3 -m meeting_harness prepare \
  --meetings-dir ../Meetings \
  --out ../Meetings/2026-01-15/.harness/run-001 \
  --language en
```

The harness reads `.docx`, `.md`, and `.txt`, including Word tables and common timestamped speaker labels. It chooses by the meeting date in the header or filename, never by file modification time. It ignores hidden working folders and reserves `summary.md` and `digest.md` for generated reports, so they are not mistaken for source meetings. Scan `../Meetings`, not its parent, which also contains project documentation. It stops on undated files, conflicting dates, or multiple latest sources so you can select `--source` explicitly. For a conflicting or missing date, select a source and optionally supply `--meeting-date YYYY-MM-DD`. Review the extracted speaker labels in `bundle.json`, especially with unfamiliar transcript formats. Audio transcription, scanned documents, and slide interpretation are outside this version.

### Work with an assistant

Give the private `prompt.txt` and `summary.schema.json` to an assistant approved to process your material. Save its JSON response as `summary.json` in the same private run folder, then check and render it:

```sh
python3 -m meeting_harness check \
  --bundle ../Meetings/2026-01-15/.harness/run-001/bundle.json \
  --summary ../Meetings/2026-01-15/.harness/run-001/summary.json

python3 -m meeting_harness render \
  --bundle ../Meetings/2026-01-15/.harness/run-001/bundle.json \
  --summary ../Meetings/2026-01-15/.harness/run-001/summary.json \
  --out ../Meetings/2026-01-15/summary.md
```

Use `--language zh` during preparation for Chinese summary content. Section labels currently remain in English. The source quotations retain their original language.

### Run your chosen model automatically

`run` accepts a trusted command as an argument array. The command must read the prompt from standard input and write exactly one summary JSON object to standard output. Write progress messages to standard error. Replace the illustrative executable below with your own working adapter:

```sh
python3 -m meeting_harness run \
  --meetings-dir ../Meetings \
  --out ../Meetings/2026-01-15/.harness/run-002 \
  --command-json '["your-model-adapter", "--json"]' \
  --attempts 2 --timeout 300
```

Automatic runs write `digest.md` inside the hidden working folder. Use the same `render` command above with `run-002` to place the finished brief beside its transcript as `summary.md`. When revising an existing brief, archive its previous `summary.md` inside `.harness/` before rendering the replacement; previous outputs are never overwritten.

No model is bundled, and no cloud service is called by the harness itself. **The selected adapter receives the transcript.** A cloud adapter may transmit it; local storage does not mean local inference. Choose a local model adapter when material must remain on your machine. The adapter is a trusted executable, not sandboxed by this project; do not use a command taken from meeting material.

## What the harness does

```text
select meeting -> extract speaker-labelled source -> build prompt and contract
  -> draft JSON -> check evidence and attribution -> bounded repair if needed
  -> private cited brief and run receipt -> human interpretation review
```

Each source segment receives a stable ID for that extraction and each run records the original file checksum. A model response must match the meeting metadata and cite exact snippets of those segments. There is no silent truncation: a prompt over the configured limit stops before the model command is launched. Increase `--max-prompt-chars` deliberately for a model with enough context; automatic chunking is not implemented.

The checks reject missing evidence, unknown speakers, quotes assigned to the wrong person, omitted participants, mismatched sources, invalid follow-up references, and claims of agreement without direct evidence from at least two named participants. All named speakers must be covered. If someone only acknowledges others, say that no substantive position was recorded and cite their acknowledgment.

`explicit` means a clearly recorded agreement among the named participants; `partial` means limited shared ground. Neither means unanimity unless the source explicitly establishes it. `none_recorded` is a valid outcome. Open questions and disagreements are represented separately. A disagreement needs at least two attributed positions. Exact matching proves where text came from; **it cannot prove that a quote supports the interpretation**. Check the cited brief before relying on it or sharing it.

The prompt treats the transcript as untrusted source data. Instructions embedded in it are not permission to change the workflow, disclose material, execute commands, or contact participants. The harness never sends emails or publishes summaries.

Run folders contain private source extracts, prompts, every model response, standard error, and either a success receipt or validation failure details. Treat the entire folder as confidential. Failed validation never produces a final brief. Manual `check` and `render` validate the provided bundle; they assume you have preserved that original generated bundle unchanged.

## Keep publication separate

Only code, generic prompts, tests, and explicitly fictional examples belong in this repository. Original material, participant identities, real quotations, summaries, and model logs must remain private. Do not move a real digest into the public project even if you omit the original transcript.

The output guard blocks writing into this source checkout except its ignored `private/` directory. External private folders are preferable. `.gitignore` reduces accidental additions. `public-files.json` is an explicit list of approved public paths, and the audit checks the Git index **and reachable commit history**, including files force-added despite ignore rules:

```sh
python3 tools/audit_public.py
```

Run it before every push. The audit also checks common credential patterns and rejects binary/media files. It is a backstop, not a comprehensive confidentiality detector. Review new public files yourself before adding them to the allowlist. Removing a leaked file from the latest commit does not remove it from earlier history.

## Iterate

- Change the summary questions and output contract in `meeting_harness/prompts.py`.
- Change evidence rules and report formatting in `meeting_harness/review.py`.
- Add transcript formats or date rules in `meeting_harness/ingest.py`.
- Change retries, limits, or model execution in `meeting_harness/cli.py`.
- Add fictional regression cases in `tests/`; never use real meetings as public test fixtures.

Tests exercise successful runs, repair and rejection paths, source selection, Word extraction, attribution, agreement checks, privacy boundaries, and command timeouts. The GitHub workflow runs the tests and publication audit. There are no model-quality scores or validated claims of improved summary accuracy in this initial version.
