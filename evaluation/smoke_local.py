"""Independent local contract checks; does not contact AML or produce AML scores."""
import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path


def run(base_url, token=''):
    def call(path, payload=None, expected=200):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(base_url.rstrip('/') + path,
                          data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                status, result = response.status, json.load(response)
        except HTTPError as exc:
            status, result = exc.code, json.load(exc)
        assert status == expected, (path, status, result)
        return result

    uid = 'local:' + uuid.uuid4().hex
    add = {'request_id': uid + ':r1', 'user_id': uid, 'session_id': 'session-one',
           'messages': [{'role': 'user', 'content': 'I moved to Hangzhou in 2025.', 'timestamp': 1704067200000}]}
    checks = []
    def passed(name):
        checks.append({'check': name, 'passed': True})

    assert call('/health')['status'] == 'ok'
    passed('health')
    assert call('/add', add) == {'success': True, 'request_id': add['request_id'],
                                 'user_id': uid, 'session_id': 'session-one'}
    query = {'query': 'Hangzhou', 'user_id': uid, 'top_k': 100}
    first = call('/search', query)['data']
    assert len(first) == 1 and first[0]['content'] == add['messages'][0]['content']
    passed('persisted write and immediate retrieval')
    call('/add', add)
    assert call('/search', query)['data'] == first
    passed('idempotent retry and stable IDs')
    conflicting = dict(add, messages=[{'role': 'user', 'content': 'Different content'}])
    call('/add', conflicting, 409)
    passed('conflicting retry rejected')
    second = dict(add, request_id=uid + ':r2', session_id='session-two',
                  messages=[{'role': 'assistant', 'content': 'Hangzhou has West Lake.'},
                            {'role': 'user', 'content': '我现在住在杭州。'}])
    call('/add', second)
    all_results = call('/search', query)['data']
    assert len(all_results) == 2
    assert all_results[0]['score'] >= all_results[1]['score']
    assert len(call('/search', dict(query, top_k=1))['data']) == 1
    passed('cross-session retrieval, ranking and top_k')
    assert call('/search', dict(query, user_id=uid + ':other')) == {'data': []}
    assert call('/search', dict(query, query='zzzzunmatched')) == {'data': []}
    passed('user isolation and empty results')
    assert call('/search', dict(query, query='杭州', options=['杭州', '北京']))['data']
    passed('CJK retrieval and top-level options')
    call('/search', dict(query, top_k='100'), 422)
    call('/search', dict(query, query=[{'type': 'text', 'text': 'Hangzhou'}]), 422)
    call('/add', dict(add, messages=[{'role': 'system', 'content': 'invalid'}]), 422)
    passed('invalid types, roles and unsupported multimodal rejected')
    return {'kind': 'local-contract-validation', 'aml_contacted': False,
            'timestamp': datetime.now(timezone.utc).isoformat(), 'user_id': uid, 'checks': checks}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', default='/home/xhy/runtime/memory-eval/local-validation.json')
    args = parser.parse_args()
    report = run(args.base_url, os.getenv('MEMORY_API_KEY', ''))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))

