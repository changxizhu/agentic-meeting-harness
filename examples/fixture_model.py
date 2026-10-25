"""Offline fictional demonstration of every stage; this is not a language model."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meeting_harness.review import artifact_hash


def parse_prompt(prompt):
    stage = re.search(r'^STAGE: (\w+)$', prompt, re.M).group(1)
    payload = json.loads(prompt.rsplit('UNTRUSTED INPUT (JSON DATA):\n', 1)[1])
    return stage, payload


def stage_output(stage, bundle, context):
    meta = {key: bundle['meeting'][key] for key in ('title', 'date', 'source_sha256')}
    def ev(sid, quote):
        return [{'segment_id': sid, 'quote': quote}]
    a = ev('S0003', 'We should hold a small workshop before planning a large event.')
    b = ev('S0004', 'I agree with a small workshop, but I prefer an evening session.')
    c = ev('S0005', 'I prefer a weekend session because volunteers have different schedules.')
    d = ev('S0006', 'We have not chosen a time. Let us compare volunteer availability next time.')
    if stage == 'discussion':
        return {'schema_version': 2, 'meeting': meta,
                'rounds': [{'id': 'R1', 'presenter': 'Alex', 'topic_ids': ['T1', 'T2']}],
                'topics': [
                    {'id': 'T1', 'round_id': 'R1', 'presenter': 'Alex', 'title': 'Workshop scope',
                     'segment_ids': ['S0003', 'S0004'], 'presentation': {'summary': 'Alex proposes starting with a small workshop.', 'evidence': a},
                     'discussion': [{'id': 'T1.C1', 'speaker': 'Blair', 'kind': 'support', 'reply_to': 'presentation',
                                     'summary': 'Blair supports the small workshop.', 'evidence': b}],
                     'outcome': {'status': 'partial_agreement', 'summary': 'Alex and Blair support a small workshop; no view on size is recorded from Casey.',
                                 'participants': ['Alex', 'Blair'], 'evidence': a + b}, 'unresolved': [], 'recorded_next_steps': []},
                    {'id': 'T2', 'round_id': 'R1', 'presenter': 'Blair', 'title': 'Workshop timing',
                     'segment_ids': ['S0004', 'S0005', 'S0006'], 'presentation': {'summary': 'Blair prefers an evening session.', 'evidence': b},
                     'discussion': [
                         {'id': 'T2.C1', 'speaker': 'Casey', 'kind': 'suggestion', 'reply_to': 'presentation',
                          'summary': 'Casey prefers a weekend session for volunteer availability.', 'evidence': c},
                         {'id': 'T2.C2', 'speaker': 'Alex', 'kind': 'response', 'reply_to': 'T2.C1',
                          'summary': 'Alex says timing is undecided and proposes checking availability next time.', 'evidence': d}],
                     'outcome': {'status': 'open', 'summary': 'No time was selected.', 'participants': [], 'evidence': d},
                     'unresolved': [{'id': 'T2.U1', 'kind': 'disagreement', 'summary': 'Evening versus weekend timing.',
                                     'positions': [{'speaker': 'Blair', 'view': 'Evening.', 'evidence': b},
                                                   {'speaker': 'Casey', 'view': 'Weekend.', 'evidence': c}], 'evidence': b + c}],
                     'recorded_next_steps': [{'id': 'T2.N1', 'proposer': 'Alex', 'proposal': 'Compare volunteer availability next time.', 'evidence': d}]}],
                'excluded_segments': [{'segment_ids': ['S0001', 'S0002'], 'reason': 'metadata', 'note': 'Fictional title and date.'}],
                'limitations': ['Fictional demonstration only; no actual meeting or independently evaluated model.']}
    discussion = context['discussion']
    if stage == 'coverage_review':
        return {'schema_version': 2, 'meeting': meta, 'discussion_sha256': artifact_hash(discussion), 'status': 'pass',
                'checked_topic_ids': [x['id'] for x in discussion['topics']],
                'checked_segment_ids': [x['id'] for x in bundle['segments']], 'issues': []}
    if stage == 'brief':
        topics = []
        for t in discussion['topics']:
            topics.append({'id': t['id'], 'title': t['title'], 'presentation': t['presentation']['summary'],
                           'discussion': [{'id': c['id'], 'summary': c['summary']} for c in t['discussion']],
                           'outcome': t['outcome']['summary'],
                           'unresolved': [{'id': u['id'], 'summary': u['summary']} for u in t['unresolved']],
                           'recorded_next_steps': [{'id': n['id'], 'proposal': n['proposal']} for n in t['recorded_next_steps']]})
        return {'schema_version': 2, 'meeting': meta, 'discussion_sha256': artifact_hash(discussion),
                'coverage_sha256': artifact_hash(context['coverage']), 'topics': topics}
    if stage == 'concise':
        # These paragraphs are independently written for this invented example.
        # Real adapters must synthesize the meaning rather than replay each turn.
        prose = {
            'Workshop scope': ('Alex suggests testing the idea with a small workshop.',
                               'Blair backs the smaller starting scope.',
                               'Their agreement is limited to scope; Casey gives no size preference.'),
            'Workshop timing': ('Blair suggests meeting in the evening.',
                                'Casey instead favours a weekend for volunteers, and Alex proposes comparing availability.',
                                'The evening-versus-weekend choice remains open.')}
        topics = []
        for topic in discussion['topics']:
            presentation, comments, outcome = prose.get(
                topic['title'], (topic['presentation']['summary'], 'No comments were recorded.', topic['outcome']['summary']))
            topics.append({'id': topic['id'], 'title': topic['title'], 'presentation': presentation,
                           'comments': comments, 'outcome': outcome,
                           'comment_ids': [comment['id'] for comment in topic['discussion']],
                           'unresolved_ids': [item['id'] for item in topic['unresolved']]})
        return {'schema_version': 2, 'meeting': meta, 'discussion_sha256': artifact_hash(discussion),
                'brief_sha256': artifact_hash(context['brief']), 'topics': topics}
    if stage == 'agenda':
        return {'schema_version': 2, 'meeting': meta, 'discussion_sha256': artifact_hash(discussion),
                'brief_sha256': artifact_hash(context['brief']),
                'items': [{'id': 'A1', 'kind': 'meeting_proposal', 'title': 'Compare volunteer availability.',
                           'reason': 'Alex explicitly suggested this to resolve the scheduling difference.',
                           'based_on': ['T2', 'T2.U1'], 'recorded_proposal_id': 'T2.N1'}], 'deferred_recorded_proposals': []}
    raise ValueError('Unknown stage')


def main():
    stage, payload = parse_prompt(sys.stdin.read())
    fixture = Path(__file__).with_name('2026-01-15-fictional.md')
    if payload['bundle']['meeting']['source_sha256'] != hashlib.sha256(fixture.read_bytes()).hexdigest():
        print('This fictional adapter does not process real meeting material.', file=sys.stderr)
        return 2
    print(json.dumps(stage_output(stage, payload['bundle'], payload['context']), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
