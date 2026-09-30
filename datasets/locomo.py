"""Public original LoCoMo adapter. Gold annotations stay in the evaluator."""
import re
from datetime import datetime, timezone


def evidence_ids(value):
    if not isinstance(value, list):
        return set()
    return {item for text in value if isinstance(text, str)
            for item in re.findall(r'D\d+:\d+', text)}


def sessions(sample):
    conversation = sample['conversation']
    names = sorted((name for name in conversation if re.fullmatch(r'session_\d+', name)),
                   key=lambda name: int(name.split('_')[1]))
    for name in names:
        source_date = conversation.get(name + '_date_time')
        timestamp = None
        if source_date:
            # Source dates have no timezone: use UTC as an explicit local convention.
            timestamp = int(datetime.strptime(source_date, '%I:%M %p on %d %B, %Y')
                            .replace(tzinfo=timezone.utc).timestamp() * 1000)
        turns = []
        for turn in conversation[name]:
            if not isinstance(turn.get('text'), str) or not turn['text'].strip():
                raise ValueError(f'Missing source text: {name}, {turn.get("dia_id")}')
            message = {'role': 'user' if turn['speaker'] == conversation['speaker_a'] else 'assistant',
                       'content': f"{turn['speaker']}: {turn['text']}"}
            if timestamp is not None:
                message['timestamp'] = timestamp
            turns.append((turn['dia_id'], message))
        yield name, turns
