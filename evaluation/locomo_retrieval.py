"""Local evidence retrieval evaluation via real AML-shaped Add/Search HTTP calls."""
import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from datasets.locomo import evidence_ids, sessions


def score_evidence(gold, retrieved, ks):
    metrics = {}
    for k in ks:
        hits = gold & set(retrieved[:k])
        metrics[str(k)] = {'recall': len(hits) / len(gold),
                           'hit': int(bool(hits)), 'all_evidence': int(hits == gold)}
    return metrics


def run(args):
    raw = Path(args.dataset).read_bytes()
    data = json.loads(raw)
    selected = data[:args.limit_samples] if args.limit_samples else data
    run_id = 'locomo-local-' + uuid.uuid4().hex
    output = Path(args.output_root) / run_id
    output.mkdir(parents=True, exist_ok=False)
    ks = sorted(set(args.ks))
    if not ks or min(ks) < 1 or max(ks) > args.top_k:
        raise ValueError('ks must be positive and <= top_k')
    token = os.getenv('MEMORY_API_KEY', '')
    def call(path, payload):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(args.base_url.rstrip('/') + path, data=json.dumps(payload).encode(), headers=headers)
        start = time.perf_counter()
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
        return result, time.perf_counter() - start

    records, mapping, add_times, search_times = [], [], [], []
    try:
        with (output / 'questions.jsonl').open('w') as traces:
            for sample_index, sample in enumerate(selected):
                uid = f'{run_id}:sample:{sample_index}'
                id_to_source, valid_ids = {}, set()
                for session_name, turns in sessions(sample):
                    for chunk_index, start in enumerate(range(0, len(turns), args.chunk_size)):
                        chunk = turns[start:start + args.chunk_size]
                        rid = f'{uid}:{session_name}:chunk:{chunk_index}'
                        payload = {'request_id': rid, 'user_id': uid,
                                   'session_id': f'{uid}:{session_name}', 'messages': [msg for _, msg in chunk]}
                        result, elapsed = call('/add', payload)
                        if result != {'success': True, 'request_id': rid, 'user_id': uid,
                                      'session_id': payload['session_id']}:
                            raise ValueError('Add response does not echo the contract')
                        add_times.append(elapsed)
                        for index, (source_id, msg) in enumerate(chunk):
                            # Stable-ID convention of this specific baseline. Not an AML requirement.
                            identity = json.dumps([uid, rid, index])
                            memory_id = 'mem_' + hashlib.sha256(identity.encode()).hexdigest()
                            if source_id in valid_ids:
                                raise ValueError('Duplicate source dialogue ID')
                            valid_ids.add(source_id)
                            id_to_source[memory_id] = source_id
                            mapping.append({'user_id': uid, 'memory_id': memory_id, 'dia_id': source_id,
                                            'session_id': payload['session_id']})
                qas = sample['qa'][:args.limit_questions] if args.limit_questions else sample['qa']
                for question_index, qa in enumerate(qas):
                    gold = evidence_ids(qa.get('evidence'))
                    category = qa.get('category')
                    if category == 5:
                        reason = 'adversarial_category_5'
                    elif not gold:
                        reason = 'no_evidence_annotation'
                    elif gold - valid_ids:
                        reason = 'unresolved_evidence_ids'
                    else:
                        reason = None
                    record = {'sample_index': sample_index, 'sample_id': sample.get('sample_id'),
                              'question_index': question_index, 'category': category,
                              'question': qa['question'], 'gold_evidence': sorted(gold),
                              'excluded_reason': reason}
                    # Query even excluded questions to verify the entire HTTP path, but do not score them.
                    result, elapsed = call('/search', {'query': qa['question'], 'user_id': uid, 'top_k': args.top_k})
                    search_times.append(elapsed)
                    candidates = result.get('data')
                    if not isinstance(candidates, list) or len(candidates) > args.top_k:
                        raise ValueError('Invalid Search data array')
                    retrieved = []
                    for candidate in candidates:
                        if candidate.get('id') not in id_to_source or not candidate.get('content'):
                            raise ValueError('Unknown memory ID or missing content; baseline mapping incompatible')
                        retrieved.append(id_to_source[candidate['id']])
                    record.update(retrieved_evidence=retrieved, search_seconds=elapsed,
                                  metrics=None if reason else score_evidence(gold, retrieved, ks))
                    records.append(record)
                    traces.write(json.dumps(record, ensure_ascii=False) + '\n')
                print(f'Sample {sample_index + 1}/{len(selected)}: {len(valid_ids)} messages, {len(qas)} queries', flush=True)
        eligible = [r for r in records if r['metrics'] is not None]
        def aggregate(rows):
            return {str(k): {metric: sum(r['metrics'][str(k)][metric] for r in rows) / len(rows)
                            for metric in ('recall', 'hit', 'all_evidence')} for k in ks} if rows else {}
        excluded = {}
        for r in records:
            if r['excluded_reason']:
                reason = r['excluded_reason']
                excluded[reason] = excluded.get(reason, 0) + 1
        summary = {'status': 'completed', 'run_id': run_id, 'kind': 'local-locomo-evidence-retrieval',
                   'aml_contacted': False, 'dataset_sha256': hashlib.sha256(raw).hexdigest(),
                   'dataset': args.dataset, 'base_url': args.base_url, 'samples': len(selected),
                   'messages_written': len(mapping), 'questions_queried': len(records),
                   'questions_scored': len(eligible), 'excluded': excluded, 'top_k': args.top_k,
                   'metrics': aggregate(eligible),
                   'by_category': {str(cat): aggregate([r for r in eligible if r['category'] == cat])
                                   for cat in sorted({r['category'] for r in eligible})},
                   'add_requests': len(add_times), 'mean_add_seconds': sum(add_times) / len(add_times),
                   'mean_search_seconds': sum(search_times) / len(search_times),
                   'timestamp': datetime.now(timezone.utc).isoformat(),
                   'notes': ['Original public LoCoMo; not AML locomo-refined or official scoring.',
                             'Recall/Hit/All-evidence are macro averages over eligible questions.',
                             'Mapping depends on SQLiteBM25 stable IDs; future backends need another provenance mapping.',
                             'Whole sessions chunked at 20 messages; not exact AML word-boundary splitting.',
                             'Source dates have no timezone; interpreted as UTC.',
                             'Gold evidence remains evaluator-only; all original dialogue messages are ingested.']}
        (output / 'mapping.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print('Output:', output)
        return summary
    except Exception as exc:
        (output / 'failure.json').write_text(json.dumps({'status': 'failed', 'run_id': run_id,
                                                       'error': str(exc)}, ensure_ascii=False, indent=2))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', default='/data/locomo10/locomo10.json')
    parser.add_argument('--base-url', default='http://127.0.0.1:9001')
    parser.add_argument('--output-root', default='/runtime/locomo-retrieval')
    parser.add_argument('--top-k', type=int, default=100)
    parser.add_argument('--ks', type=int, nargs='+', default=[1, 5, 10, 20, 100])
    parser.add_argument('--chunk-size', type=int, default=20)
    parser.add_argument('--limit-samples', type=int, default=0)
    parser.add_argument('--limit-questions', type=int, default=0)
    args = parser.parse_args()
    if args.chunk_size <= 0 or args.limit_samples < 0 or args.limit_questions < 0:
        parser.error('chunk-size must be positive; limits must be nonnegative')
    run(args)
