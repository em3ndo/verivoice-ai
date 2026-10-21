"""Offline metrics: saved provider timings and explicit synthetic policy scenarios.
Run from repository root: .venv/bin/python output/benchmarks/measure_resume_metrics.py
No API calls, biometric accuracy claims, or account changes.
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from verivoice.confidence import Confidence

records = json.loads((ROOT / 'data/call-diagnostics.json').read_text())
pending = {}
observations = []
for event in records:
    call = event['call']
    if event['event'] == 'hiya_started':
        pending[call] = event
    elif event['event'] == 'language_result' and call in pending:
        start = pending.pop(call)
        observations.append({
            'clip_seconds': start['seconds'],
            'combined_seconds': event['time'] - start['time'],
            'hiya_seconds': event['hiya_ms'] / 1000,
            'soniox_seconds': event['soniox_ms'] / 1000,
            'language_aggregation_ms': event['aggregation_ms'],
        })

def summary(values):
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values), minimum=min(values), maximum=max(values)) if values else None

scenarios = []
for component in ('identity', 'synthesis', 'replay', 'language'):
    for low in (0.0, 0.2, 0.4, 0.6):
        state = Confidence()
        state.update(.95, .95, .95, .95)
        updates = 0
        final = None
        for i in range(1, 21):
            values = dict(identity=.95, synthesis=.95, replay=.95, language=.95)
            values[component] = low
            state.update(*values.values())
            notice = state.notification(float(i))
            if notice and notice['type'] == 'removed':
                updates, final = i, state.c
                break
        scenarios.append(dict(component=component, sustained_raw_score=low,
            completed_observations_until_removal=updates or None, confidence_at_removal=final,
            note='Synthetic score sequence; not a voice attack or accuracy test.'))

result = {
    'source': 'data/call-diagnostics.json (bounded recent history, no ground-truth labels)',
    'scope': 'Successful completed windows only; failures counted separately. No new live API requests.',
    'calls_with_completed_windows': len({e['call'] for e in records if e['event']=='language_result'}),
    'completed_windows': observations,
    'combined_verification_seconds': summary([x['combined_seconds'] for x in observations]),
    'language_aggregation_ms': summary([x['language_aggregation_ms'] for x in observations]),
    'observed_call_errors': sum(e['event']=='error' for e in records),
    'observed_update_failures': sum(e['event']=='voiceprint_update_failed' for e in records),
    'estimated_concurrency_saving_percent': 100*(1-sum(x['combined_seconds'] for x in observations)/sum(x['hiya_seconds']+x['soniox_seconds'] for x in observations)) if observations else None,
    'concurrency_note': 'Arithmetic comparison to sum of measured provider times; not a sequential A/B benchmark.',
    'policy_scenarios': scenarios,
    'limitations': ['No measured fraud detection rate, FAR, FRR, or EER without labeled audio.',
        'Verification latency excludes audio collection, initial prompt, queue wait, and browser receipt.',
        'Removal timing is observations, not seconds; missing speech and smoothing affect response time.',
        'Raw synthesis is adjusted to 0.5 + 0.5*S, so persistent S >= 0.4 alone cannot cross below 0.7.'],
}
path = ROOT / 'output/benchmarks/resume_metrics.json'
path.write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
