import unittest
from datasets.locomo import evidence_ids, sessions
from evaluation.locomo_retrieval import score_evidence


class LoCoMoTests(unittest.TestCase):
    def test_multiple_ids_and_empty_annotation(self):
        self.assertEqual(evidence_ids(['D8:6; D9:17', 'D8:6']), {'D8:6', 'D9:17'})
        self.assertEqual(evidence_ids([]), set())

    def test_recall_and_full_coverage_differ(self):
        result = score_evidence({'D1:1', 'D1:2'}, ['D1:1', 'D1:3', 'D1:2'], [1, 3])
        self.assertEqual(result['1'], {'recall': .5, 'hit': 1, 'all_evidence': 0})
        self.assertEqual(result['3'], {'recall': 1, 'hit': 1, 'all_evidence': 1})

    def test_source_order_speaker_and_gold_separation(self):
        sample = {'qa': [{'answer': 'SECRET'}], 'conversation': {'speaker_a': 'A', 'speaker_b': 'B',
                  'session_2': [{'speaker': 'B', 'dia_id': 'D2:1', 'text': 'world'}],
                  'session_1': [{'speaker': 'A', 'dia_id': 'D1:1', 'text': 'hello'}]}}
        adapted = list(sessions(sample))
        self.assertEqual([name for name, _ in adapted], ['session_1', 'session_2'])
        self.assertEqual(adapted[0][1][0][1], {'role': 'user', 'content': 'A: hello'})
        self.assertNotIn('SECRET', str(adapted))
