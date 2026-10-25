"""Only invented meeting material is used to exercise the v2 workflow."""
import copy
import hashlib
import json
import unittest

from meeting_harness.review import (artifact_hash, render_concise_markdown, render_markdown, validate_agenda,
                                    validate_brief, validate_bundle, validate_coverage, validate_discussion)


def fixture():
    meta = {"title": "Synthetic planning meeting", "date": "2026-01-12", "source_sha256": "a" * 64}
    segments = [
        (None, "Synthetic planning meeting 2026-01-12"),
        ("Alice", "Our pilot should track cost. I propose we discuss the budget next time."),
        ("Bob", "I agree to track cost. Can we run without supervision?"),
        ("Alice", "I agree to track cost. We need supervision until the audit is complete."),
        ("Bob", "I still prefer no supervision."),
        ("Bob", "My separate topic is the release calendar."),
        ("Alice", "Which date will be feasible?"),
        ("Bob", "We have no date yet. I propose a scheduling discussion next time."),
        ("Charlie", "Thanks."),
    ]
    bundle = {"schema_version": 1, "meeting": {**meta, "date_basis": "explicit source date"},
              "segments": [{"id": f"S{i:04d}", "speaker": name, "timestamp": None, "text": text}
                           for i, (name, text) in enumerate(segments, 1)], "warnings": []}

    def ev(sid, quote):
        return {"segment_id": sid, "quote": quote}

    alice = ev("S0004", "We need supervision until the audit is complete.")
    bob = ev("S0005", "I still prefer no supervision.")
    discussion = {"schema_version": 2, "meeting": meta,
        "rounds": [{"id": "R1", "presenter": "Alice", "topic_ids": ["T1"]},
                   {"id": "R2", "presenter": "Bob", "topic_ids": ["T2"]}],
        "topics": [
            {"id": "T1", "round_id": "R1", "presenter": "Alice", "title": "Pilot cost and supervision",
             "segment_ids": ["S0002", "S0003", "S0004", "S0005"],
             "presentation": {"summary": "Track cost and compare supervision.",
                              "evidence": [ev("S0002", "Our pilot should track cost.")]},
             "discussion": [
                 {"id": "T1.C1", "speaker": "Bob", "kind": "question", "reply_to": "presentation",
                  "summary": "Asks whether supervision is needed.", "evidence": [ev("S0003", "Can we run without supervision?")]},
                 {"id": "T1.C2", "speaker": "Alice", "kind": "response", "reply_to": "T1.C1",
                  "summary": "An audit must finish before supervision is removed.", "evidence": [alice]},
                 {"id": "T1.C3", "speaker": "Bob", "kind": "challenge", "reply_to": "T1.C2",
                  "summary": "Still prefers an unsupervised pilot.", "evidence": [bob]}],
             "outcome": {"status": "partial_agreement", "summary": "Both agree on cost tracking, not supervision.",
                         "participants": ["Alice", "Bob"], "evidence": [
                             ev("S0003", "I agree to track cost."), ev("S0004", "I agree to track cost.")]},
             "unresolved": [{"id": "T1.U1", "kind": "disagreement", "summary": "Whether supervision is needed.",
                              "positions": [{"speaker": "Alice", "view": "Retain supervision pending audit.", "evidence": [alice]},
                                            {"speaker": "Bob", "view": "Remove supervision.", "evidence": [bob]}],
                              "evidence": [alice, bob]}],
             "recorded_next_steps": [{"id": "T1.N1", "proposer": "Alice", "proposal": "Discuss the budget.",
                                      "evidence": [ev("S0002", "I propose we discuss the budget next time.")]}]},
            {"id": "T2", "round_id": "R2", "presenter": "Bob", "title": "Release calendar",
             "segment_ids": ["S0006", "S0007", "S0008"],
             "presentation": {"summary": "Discuss the release calendar.", "evidence": [ev("S0006", "My separate topic is the release calendar.")]},
             "discussion": [{"id": "T2.C1", "speaker": "Alice", "kind": "question", "reply_to": "presentation",
                             "summary": "Asks for a feasible date.", "evidence": [ev("S0007", "Which date will be feasible?")]},
                            {"id": "T2.C2", "speaker": "Bob", "kind": "response", "reply_to": "T2.C1",
                             "summary": "No date has been set.", "evidence": [ev("S0008", "We have no date yet.")]}],
             "outcome": {"status": "open", "summary": "The release date remains undecided.", "participants": [],
                         "evidence": [ev("S0008", "We have no date yet.")]},
             "unresolved": [{"id": "T2.U1", "kind": "open_question", "summary": "Choose the date.", "positions": [],
                              "evidence": [ev("S0007", "Which date will be feasible?")]}],
             "recorded_next_steps": [{"id": "T2.N1", "proposer": "Bob", "proposal": "Discuss scheduling.",
                                      "evidence": [ev("S0008", "I propose a scheduling discussion next time.")]}]}],
        "excluded_segments": [{"segment_ids": ["S0001"], "reason": "metadata", "note": "Meeting header."},
                              {"segment_ids": ["S0009"], "reason": "acknowledgment", "note": "A short acknowledgment."}],
        "limitations": ["Synthetic examples only."]}
    coverage = {"schema_version": 2, "meeting": copy.deepcopy(meta), "discussion_sha256": artifact_hash(discussion),
                "status": "pass", "checked_topic_ids": ["T1", "T2"],
                "checked_segment_ids": [s["id"] for s in bundle["segments"]], "issues": []}
    brief = {"schema_version": 2, "meeting": copy.deepcopy(meta), "discussion_sha256": artifact_hash(discussion),
             "coverage_sha256": artifact_hash(coverage), "topics": [
                 {"id": t["id"], "title": t["title"], "presentation": t["presentation"]["summary"],
                  "discussion": [{"id": q["id"], "summary": q["summary"]} for q in t["discussion"]],
                  "outcome": t["outcome"]["summary"],
                  "unresolved": [{"id": q["id"], "summary": q["summary"]} for q in t["unresolved"]],
                  "recorded_next_steps": [{"id": q["id"], "proposal": q["proposal"]} for q in t["recorded_next_steps"]]}
                 for t in discussion["topics"]]}
    agenda = {"schema_version": 2, "meeting": copy.deepcopy(meta), "discussion_sha256": artifact_hash(discussion),
              "brief_sha256": artifact_hash(brief), "items": [
                  {"id": "A1", "kind": "meeting_proposal", "title": "Budget", "reason": "Explicitly proposed by Alice.",
                   "based_on": ["T1"], "recorded_proposal_id": "T1.N1"},
                  {"id": "A2", "kind": "meeting_proposal", "title": "Scheduling", "reason": "Explicitly proposed by Bob.",
                   "based_on": ["T2"], "recorded_proposal_id": "T2.N1"}], "deferred_recorded_proposals": []}
    return bundle, discussion, coverage, brief, agenda


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.bundle, self.discussion, self.coverage, self.brief, self.agenda = fixture()

    def errors(self):
        return validate_discussion(self.discussion, self.bundle)

    def assert_error(self, fragment, errors=None):
        errors = self.errors() if errors is None else errors
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_valid_full_workflow(self):
        self.assertEqual(validate_bundle(self.bundle), [])
        self.assertEqual(self.errors(), [])
        self.assertEqual(validate_coverage(self.coverage, self.discussion, self.bundle), [])
        self.assertEqual(validate_brief(self.brief, self.discussion, self.coverage, self.bundle), [])
        self.assertEqual(validate_agenda(self.agenda, self.discussion, self.brief, self.bundle), [])

    def test_hash_is_canonical_utf8_json(self):
        left, right = {"b": "中文", "a": 1}, {"a": 1, "b": "中文"}
        self.assertEqual(artifact_hash(left), artifact_hash(right))
        expected = hashlib.sha256('{"a":1,"b":"中文"}'.encode()).hexdigest()
        self.assertEqual(artifact_hash(left), expected)
        with self.assertRaises(ValueError):
            artifact_hash({"bad": float("nan")})

    def test_more_than_six_topics_is_allowed(self):
        topic = self.discussion["topics"][0]
        self.discussion["topics"] = []
        self.discussion["rounds"] = []
        self.discussion["excluded_segments"] = []
        for i in range(8):
            item = copy.deepcopy(topic)
            tid, rid = f"T{i + 1}", f"R{i + 1}"
            sid = f"S{i + 1:04d}"
            # One complete invented topic per segment. Nothing limits this map to six.
            self.bundle["segments"][i]["speaker"] = "Alice"
            item.update(id=tid, round_id=rid, segment_ids=[sid], discussion=[], unresolved=[], recorded_next_steps=[])
            item["presentation"]["evidence"] = [{"segment_id": sid, "quote": self.bundle["segments"][i]["text"]}]
            item["outcome"] = {"status": "no_conclusion", "summary": "No conclusion.", "participants": [], "evidence": []}
            self.discussion["topics"].append(item)
            self.discussion["rounds"].append({"id": rid, "presenter": "Alice", "topic_ids": [tid]})
        self.discussion["excluded_segments"] = [{"segment_ids": ["S0009"], "reason": "acknowledgment", "note": "Thanks."}]
        self.assertEqual(self.errors(), [])

    def test_all_source_segments_must_be_accounted_for(self):
        self.discussion["topics"][0]["segment_ids"].remove("S0005")
        self.assert_error("source segment S0005 is not mapped")
        self.assert_error("evidence must be inside topic")

    def test_mapped_and_excluded_are_mutually_exclusive(self):
        self.discussion["excluded_segments"][0]["segment_ids"].append("S0002")
        self.assert_error("cannot be both mapped and excluded")

    def test_overlapping_topic_segment_mapping_is_allowed(self):
        self.discussion["topics"][1]["segment_ids"].insert(0, "S0005")
        self.assertEqual(self.errors(), [])

    def test_source_speaker_acknowledgment_needs_no_standalone_view(self):
        self.assertEqual(self.errors(), [])  # Charlie's acknowledgment is excluded.
        self.assertNotIn("Charlie", json.dumps(self.discussion))

    def test_source_bound_metadata(self):
        self.discussion["meeting"]["source_sha256"] = "b" * 64
        self.assert_error("must match the supplied source bundle")

    def test_unknown_or_inexact_quotes(self):
        ev = self.discussion["topics"][0]["presentation"]["evidence"][0]
        ev["quote"] = "Paraphrased text."
        self.assert_error("exact substring")
        ev["segment_id"] = "S9999"
        self.assert_error("unknown source segment")

    def test_presentation_and_comments_cannot_borrow_others_quotes(self):
        topic = self.discussion["topics"][0]
        topic["presentation"]["evidence"] = copy.deepcopy(topic["discussion"][0]["evidence"])
        self.assert_error("not 'Alice'")
        topic["discussion"][0]["speaker"] = "Alice"
        self.assert_error("not 'Alice'")

    def test_proposals_and_positions_have_owner_checks(self):
        topic = self.discussion["topics"][0]
        topic["recorded_next_steps"][0]["proposer"] = "Bob"
        topic["unresolved"][0]["positions"][0]["speaker"] = "Bob"
        self.assert_error("not 'Bob'")

    def test_anonymous_source_is_not_given_an_invented_name(self):
        for segment in self.bundle["segments"]:
            if segment["speaker"] == "Alice":
                segment["speaker"] = None
        self.discussion["rounds"][0]["presenter"] = None
        topic = self.discussion["topics"][0]
        topic["presenter"] = None
        topic["discussion"][1]["speaker"] = None
        topic["unresolved"] = []
        topic["recorded_next_steps"][0]["proposer"] = None
        topic["outcome"].update(status="no_conclusion", participants=[], evidence=[])
        self.discussion["topics"][1]["discussion"][0]["speaker"] = None
        self.assertEqual(self.errors(), [])
        # A null attribution must still match source null, not silently accept Bob.
        topic["presentation"]["evidence"] = copy.deepcopy(topic["discussion"][0]["evidence"])
        self.assert_error("not None")

    def test_comment_links_must_be_earlier_and_inside_same_topic(self):
        comment = self.discussion["topics"][0]["discussion"][0]
        for target in ("T2.C1", "T1.C2", "T1.C1", "T1.C999", None, []):
            comment["reply_to"] = target
            self.assert_error("earlier comment in this topic")

    def test_comment_ids_and_valid_links_do_not_override_source_chronology(self):
        topic = self.discussion["topics"][0]
        topic["discussion"].reverse()
        for i, comment in enumerate(topic["discussion"], 1):
            comment["id"] = f"T1.C{i}"
            comment["reply_to"] = "presentation" if i == 1 else f"T1.C{i - 1}"
        self.assert_error("chronological order of their earliest cited source segment")

    def test_response_cannot_target_comment_with_later_source(self):
        comments = self.discussion["topics"][0]["discussion"]
        comments[0]["evidence"] = copy.deepcopy(comments[2]["evidence"])
        self.assertEqual(comments[1]["reply_to"], "T1.C1")
        self.assert_error("cannot target a comment whose cited source begins later")

    def test_multiple_comments_from_one_source_segment_are_allowed(self):
        comments = self.discussion["topics"][0]["discussion"]
        comments[2].update(speaker="Alice", kind="addition", evidence=copy.deepcopy(comments[1]["evidence"]))
        self.assertEqual(self.errors(), [])

    def test_response_can_target_presentation(self):
        self.discussion["topics"][0]["discussion"][1]["reply_to"] = "presentation"
        self.assertEqual(self.errors(), [])

    def test_agreement_needs_own_quotes_from_two_named_people(self):
        outcome = self.discussion["topics"][0]["outcome"]
        outcome["evidence"].pop()
        self.assert_error("no direct evidence from participant 'Alice'")
        outcome["participants"] = ["Bob", "Bob"]
        self.assert_error("at least two distinct named participants")

    def test_nonagreement_outcome_has_no_participants(self):
        self.discussion["topics"][0]["outcome"]["status"] = "answered"
        self.assert_error("must be empty without an agreement status")

    def test_only_no_conclusion_allows_empty_outcome_evidence(self):
        outcome = self.discussion["topics"][1]["outcome"]
        outcome["evidence"] = []
        self.assert_error("outcome.evidence: expected at least 1 items")
        outcome["status"] = "no_conclusion"
        self.assertEqual(self.errors(), [])

    def test_disagreement_needs_two_named_positions(self):
        self.discussion["topics"][0]["unresolved"][0]["positions"].pop()
        self.assert_error("two distinct named positions")

    def test_round_exact_topic_references(self):
        self.discussion["rounds"][0]["presenter"] = "Bob"
        self.discussion["rounds"][1]["topic_ids"] = ["T1"]
        self.assert_error("every topic exactly once")

    def test_topic_initiator_can_differ_from_main_round_presenter(self):
        topic = self.discussion["topics"][0]
        topic["presenter"] = "Bob"
        topic["presentation"]["evidence"] = [{"segment_id": "S0003", "quote": "Can we run without supervision?"}]
        self.assertEqual(self.discussion["rounds"][0]["presenter"], "Alice")
        self.assertEqual(self.errors(), [])

    def test_chronological_topic_order(self):
        first, second = self.discussion["topics"]
        first["segment_ids"], second["segment_ids"] = second["segment_ids"], first["segment_ids"]
        self.assert_error("ordered chronologically")

    def test_sequential_ids_for_all_collections(self):
        for container, key, wrong in ((self.discussion["topics"][0], "id", "T99"),
                                      (self.discussion["topics"][0]["discussion"][0], "id", "T1.C7"),
                                      (self.discussion["topics"][0]["unresolved"][0], "id", "T1.U8"),
                                      (self.discussion["topics"][0]["recorded_next_steps"][0], "id", "T1.N4")):
            container[key] = wrong
        self.assert_error("expected ordered ID T1")
        self.assert_error("expected ordered ID T1.C1")
        self.assert_error("expected ordered ID T1.U1")
        self.assert_error("expected ordered ID T1.N1")

    def test_coverage_is_separate_from_mechanical_mapping(self):
        # All segments remain mapped, but a substantive reply was dropped. Structural
        # mapping alone does not detect its meaning; coverage records that finding.
        self.discussion["topics"][0]["discussion"].pop()
        self.assertEqual(self.errors(), [])
        self.coverage.update(discussion_sha256=artifact_hash(self.discussion), status="revise", issues=[
            {"kind": "missing_response", "topic_id": "T1", "segment_ids": ["S0005"],
             "detail": "A mapped source segment contains a response absent from the discussion map."}])
        self.assertEqual(validate_coverage(self.coverage, self.discussion, self.bundle), [])
        self.brief.update(discussion_sha256=artifact_hash(self.discussion), coverage_sha256=artifact_hash(self.coverage))
        self.assert_error("coverage audit must pass", validate_brief(self.brief, self.discussion, self.coverage, self.bundle))

    def test_coverage_checks_every_topic_and_source_segment(self):
        self.coverage["checked_topic_ids"].pop()
        self.coverage["checked_segment_ids"].pop()
        errors = validate_coverage(self.coverage, self.discussion, self.bundle)
        self.assert_error("must check every topic", errors)
        self.assert_error("must check every source segment", errors)

    def test_coverage_pass_and_revise_are_consistent(self):
        self.coverage["status"] = "revise"
        self.assert_error("revise requires at least one issue", validate_coverage(self.coverage, self.discussion, self.bundle))
        self.coverage["status"] = "pass"
        self.coverage["issues"] = [{"kind": "other", "topic_id": None, "segment_ids": ["S0001"], "detail": "Review again."}]
        self.assert_error("pass requires no issues", validate_coverage(self.coverage, self.discussion, self.bundle))

    def test_later_stages_bind_exact_earlier_content(self):
        self.discussion["limitations"].append("Updated.")
        self.assert_error("must match the referenced artifact", validate_coverage(self.coverage, self.discussion, self.bundle))
        self.brief["topics"][0]["title"] = "New wording"
        self.assert_error("must match the referenced artifact", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_brief_preserves_all_topics_comments_unresolved_and_proposals(self):
        for key in ("discussion", "unresolved", "recorded_next_steps"):
            brief = copy.deepcopy(self.brief)
            brief["topics"][0][key].pop()
            self.assert_error("must preserve every original ID", validate_brief(brief, self.discussion, self.coverage, self.bundle))
        self.brief["topics"].pop()
        self.assert_error("must preserve every topic ID", validate_brief(self.brief, self.discussion, self.coverage, self.bundle))

    def test_brief_cannot_retitle_an_approved_topic(self):
        self.brief["topics"][0]["title"] = "A title that changes the topic's framing"
        self.assert_error("must preserve the approved discussion topic title",
                          validate_brief(self.brief, self.discussion, self.coverage, self.bundle))

    def test_brief_cannot_reorder_comments(self):
        self.brief["topics"][0]["discussion"].reverse()
        self.assert_error("must preserve every original ID", validate_brief(self.brief, self.discussion, self.coverage, self.bundle))

    def test_only_agenda_has_one_to_three_limit(self):
        self.agenda["items"] *= 2
        self.assert_error("expected 1–3 items", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_all_recorded_proposals_selected_or_explicitly_deferred(self):
        self.agenda["items"].pop()
        self.assert_error("T2.N1 must be selected or explicitly deferred", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))
        self.agenda["deferred_recorded_proposals"] = [{"proposal_id": "T2.N1", "reason": "Leave time for budget."}]
        self.assertEqual(validate_agenda(self.agenda, self.discussion, self.brief, self.bundle), [])

    def test_deferred_proposals_and_derived_suggestion_can_be_fully_accounted_for(self):
        self.agenda["items"][1].update(
            kind="synthesizer_suggestion", recorded_proposal_id=None,
            title="Compare approaches to supervision.", based_on=["T1", "T1.U1"],
            reason="Summarizer suggestion: this question helps address the unresolved supervision difference.")
        self.agenda["deferred_recorded_proposals"] = [
            {"proposal_id": "T2.N1", "reason": "Discuss the budget and supervision first; schedule a separate timing review."}]
        self.assertEqual(validate_agenda(self.agenda, self.discussion, self.brief, self.bundle), [])
        self.agenda["deferred_recorded_proposals"] = []
        self.assert_error("T2.N1 must be selected or explicitly deferred",
                          validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_derived_suggestions_follow_selected_recorded_proposals(self):
        self.agenda["items"].insert(0, {"id": "A1", "kind": "synthesizer_suggestion", "title": "Audit comparison",
                                       "reason": "Resolve the open question.", "based_on": ["T1.U1"], "recorded_proposal_id": None})
        for i, item in enumerate(self.agenda["items"], 1):
            item["id"] = f"A{i}"
        self.assert_error("must precede derived suggestions", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_recorded_proposals_cannot_be_fabricated_or_point_to_wrong_topic(self):
        self.agenda["items"][0]["recorded_proposal_id"] = "T9.N1"
        self.assert_error("original recorded proposal ID", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))
        self.agenda["items"][0].update(recorded_proposal_id="T1.N1", based_on=["T2"])
        self.assert_error("must include the recorded proposal's original topic", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_proposals_cannot_be_both_selected_and_deferred(self):
        self.agenda["deferred_recorded_proposals"] = [{"proposal_id": "T1.N1", "reason": "Time."}]
        self.assert_error("both selected and deferred", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_agenda_validates_full_brief_shape_without_coverage_argument(self):
        self.brief["topics"][0]["discussion"] = []
        self.agenda["brief_sha256"] = artifact_hash(self.brief)
        self.assert_error("must preserve every original ID", validate_agenda(self.agenda, self.discussion, self.brief, self.bundle))

    def test_renderer_preserves_rounds_comments_responses_before_agenda(self):
        rendered = render_markdown(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
        for text in ("Alice 的分享", "Bob 的分享", "Bob · 提问", "Alice · 回应", "针对 Bob的提问", "No date has been set.", "局部共识", "未解决分歧", "Discuss the budget.", "会上已有提议", "原文依据", "不能证明所有话题均已识别"):
            self.assertIn(text, rendered)
        self.assertLess(rendered.index("No date has been set."), rendered.index("## 下次讨论议题"))
        self.assertNotIn("T1.C1", rendered)
        self.assertNotIn("T1.N1", rendered)
        self.assertIn("> I agree to track cost.", rendered)

    def test_renderer_uses_compressed_text_and_original_attributions(self):
        self.brief["topics"][0]["discussion"][0]["summary"] = "A shorter question."
        self.agenda["brief_sha256"] = artifact_hash(self.brief)
        rendered = render_markdown(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
        self.assertIn("Bob · 提问", rendered)
        self.assertIn("A shorter question.", rendered)

    def test_concise_renderer_preserves_all_content_and_reply_relationships(self):
        rendered = render_concise_markdown(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
        for topic, compact in zip(self.discussion["topics"], self.brief["topics"]):
            for text in (compact["title"], compact["presentation"], compact["outcome"]):
                self.assertIn(text, rendered)
            for comment in compact["discussion"]:
                self.assertIn(comment["summary"], rendered)
            for issue in compact["unresolved"]:
                self.assertIn(issue["summary"], rendered)
            for issue in topic["unresolved"]:
                for position in issue["positions"]:
                    self.assertIn(f"{position['speaker']}：{position['view']}", rendered)
            for proposal in compact["recorded_next_steps"]:
                self.assertIn(proposal["proposal"], rendered)
        for text in ("Alice 的分享", "Bob 的分享", "Bob提问（针对Alice的分享）", "Alice回应（针对Bob的提问）",
                     "Bob质疑（针对Alice的回应）", "局部共识（Alice、Bob）", "未解决分歧", "开放问题"):
            self.assertIn(text, rendered)
        # The entire reply chain shares a paragraph, retaining the original order.
        first_chain = next(row for row in rendered.splitlines() if "Bob提问" in row)
        self.assertLess(first_chain.index("Bob提问"), first_chain.index("Alice回应"))
        self.assertLess(first_chain.index("Alice回应"), first_chain.index("Bob质疑"))
        self.assertLess(rendered.index("Pilot cost and supervision"), rendered.index("Release calendar"))
        self.assertLess(rendered.index("No date has been set."), rendered.index("## 下次讨论议题"))
        for item in self.agenda["items"]:
            self.assertIn(item["title"], rendered)
            self.assertIn(item["reason"], rendered)

    def test_concise_renderer_has_no_reference_or_audit_appendix(self):
        rendered = render_concise_markdown(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
        for text in ("[E1]", "S0002", "T1.C1", "T1.N1", "原文依据", "覆盖检查", "排除片段", "Source SHA-256",
                     "a" * 64, "Synthetic examples only.", "> Our pilot should track cost."):
            self.assertNotIn(text, rendered)
        referenced = render_markdown(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
        self.assertLess(len(rendered), len(referenced))
        self.assertIn("[E1]", referenced)
        self.assertIn("原文依据", referenced)

    def test_concise_renderer_preserves_deferred_proposals_and_labels_derived_agenda(self):
        self.agenda["items"][1].update(
            kind="synthesizer_suggestion", recorded_proposal_id=None,
            title="Compare supervision approaches.", based_on=["T1", "T1.U1"],
            reason="Resolve the open supervision difference.")
        self.agenda["deferred_recorded_proposals"] = [{"proposal_id": "T2.N1", "reason": "Budget first."}]
        rendered = render_concise_markdown(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
        self.assertIn("Bob：Discuss scheduling.", rendered)
        self.assertIn("会上已有提议：Budget", rendered)
        self.assertIn("总结者延伸建议：Compare supervision approaches.", rendered)
        self.assertIn("（Alice提议）", rendered)
        self.assertIn("议题入选不代表会议已作出决定。", rendered)

    def test_renderer_rejects_failed_coverage(self):
        self.coverage["status"] = "revise"
        for renderer in (render_markdown, render_concise_markdown):
            with self.subTest(renderer=renderer.__name__), self.assertRaisesRegex(ValueError, "failed validation"):
                renderer(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)

    def test_markdown_and_html_source_are_escaped(self):
        malicious = "![image](https://invalid.example/x) <script>bad()</script>"
        self.discussion["topics"][0]["title"] = malicious
        self.brief["topics"][0]["title"] = malicious
        self.coverage["discussion_sha256"] = artifact_hash(self.discussion)
        self.brief.update(discussion_sha256=artifact_hash(self.discussion), coverage_sha256=artifact_hash(self.coverage))
        self.agenda.update(discussion_sha256=artifact_hash(self.discussion), brief_sha256=artifact_hash(self.brief))
        for renderer in (render_markdown, render_concise_markdown):
            with self.subTest(renderer=renderer.__name__):
                rendered = renderer(self.discussion, self.coverage, self.brief, self.agenda, self.bundle)
                self.assertNotIn("![image]", rendered)
                self.assertNotIn("<script>", rendered)
                self.assertIn(r"\!\[image\]", rendered)

    def test_unknown_date_is_preserved(self):
        for artifact in (self.bundle, self.discussion, self.coverage, self.brief, self.agenda):
            artifact["meeting"]["date"] = None
        self.coverage["discussion_sha256"] = artifact_hash(self.discussion)
        self.brief.update(discussion_sha256=artifact_hash(self.discussion), coverage_sha256=artifact_hash(self.coverage))
        self.agenda.update(discussion_sha256=artifact_hash(self.discussion), brief_sha256=artifact_hash(self.brief))
        for renderer in (render_markdown, render_concise_markdown):
            with self.subTest(renderer=renderer.__name__):
                self.assertIn("会议日期：未记录", renderer(self.discussion, self.coverage, self.brief, self.agenda, self.bundle))

    def test_arbitrary_malformed_inputs_fail_closed_without_raising(self):
        invalid = (None, [], False, 0, "bad", {}, {"schema_version": True})
        for value in invalid:
            with self.subTest(value=value):
                self.assertTrue(validate_discussion(value, self.bundle))
                self.assertTrue(validate_discussion(self.discussion, value))
                self.assertTrue(validate_coverage(value, self.discussion, self.bundle))
                self.assertTrue(validate_coverage(self.coverage, value, self.bundle))
                self.assertTrue(validate_brief(value, self.discussion, self.coverage, self.bundle))
                self.assertTrue(validate_brief(self.brief, value, self.coverage, self.bundle))
                self.assertTrue(validate_brief(self.brief, self.discussion, value, self.bundle))
                self.assertTrue(validate_agenda(value, self.discussion, self.brief, self.bundle))
                self.assertTrue(validate_agenda(self.agenda, value, self.brief, self.bundle))
                self.assertTrue(validate_agenda(self.agenda, self.discussion, value, self.bundle))

    def test_malformed_nested_values_do_not_raise(self):
        mutations = (None, [], {}, False, 1, "")
        for i, artifact in enumerate((self.discussion, self.coverage, self.brief, self.agenda)):
            for key in artifact:
                for value in mutations:
                    broken = copy.deepcopy(artifact)
                    broken[key] = value
                    with self.subTest(stage=i, key=key, value=value):
                        errors = (validate_discussion(broken, self.bundle) if i == 0 else
                                  validate_coverage(broken, self.discussion, self.bundle) if i == 1 else
                                  validate_brief(broken, self.discussion, self.coverage, self.bundle) if i == 2 else
                                  validate_agenda(broken, self.discussion, self.brief, self.bundle))
                        self.assertIsInstance(errors, list)
        for field in self.discussion["topics"][0]:
            for value in mutations:
                broken = copy.deepcopy(self.discussion)
                broken["topics"][0][field] = value
                self.assertIsInstance(validate_discussion(broken, self.bundle), list)

    def test_duplicate_source_ids_and_bad_source_types(self):
        self.bundle["segments"].append(copy.deepcopy(self.bundle["segments"][0]))
        self.assert_error("duplicate source segment")
        for field in ("id", "text", "speaker", "timestamp"):
            for bad in ([], {}, False, 3):
                broken = copy.deepcopy(self.bundle)
                broken["segments"][0][field] = bad
                self.assertTrue(validate_discussion(self.discussion, broken))


if __name__ == "__main__":
    unittest.main()
