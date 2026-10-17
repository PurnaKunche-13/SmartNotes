"""Offline integration checks; Ollama output is mocked, rendering/PDF are real."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
import main
import syllabus_pipeline as pipeline

class InputModesTest(unittest.TestCase):
    def test_both_modes_and_pdf(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pipeline, 'ROOT', Path(directory)), patch.object(pipeline.threading, 'Thread'), patch.object(pipeline, 'generate_complete_answer', return_value='Human resource planning estimates staffing needs. It supports recruitment and training.'), patch.object(pipeline, 'Client') as client:
            client.return_value.chat.return_value = SimpleNamespace(message=SimpleNamespace(content=json.dumps({'questions':[f'Explain staffing planning aspect {i} with an example?' for i in range(15)]})))
            for endpoint, payload, expected in [('/api/topic-jobs', {'topic':'HR planning'},15),('/api/syllabus-jobs',{'syllabus':'Planning, Recruitment; Selection\nTraining'},4)]:
                response=(main.start_topic_job(main.TopicJobRequest(**payload)) if endpoint == '/api/topic-jobs' else main.start_syllabus_job(main.SyllabusJobRequest(**payload)))
                jid=response['id']
                pipeline.run_job(jid,'mock','mock')
                job=main.get_syllabus_job(jid)
                self.assertEqual(job['status'],'complete')
                self.assertEqual(job['completed'],expected)
                self.assertEqual(len(job['questions']),expected)
                pdf=main.download_handwritten_pdf(jid)
                self.assertEqual(pdf.status_code,200)
                self.assertTrue(pdf.body.startswith(b'%PDF'))
                pipeline.jobs.pop(jid)
                self.assertEqual(pipeline.load_job(jid)['status'],'complete')
                pipeline.jobs.pop(jid)
            for payload in [{'topic':'   '},{'topic':'Java','marks':3}]:
                with self.assertRaises(main.HTTPException): main.start_topic_job(main.TopicJobRequest(**payload))
    def test_numbered_and_fenced_questions(self):
        numbered='\n'.join(f'{i}. What is Java concept {i}?' for i in range(1,16))
        self.assertEqual(len(pipeline.parse_questions(numbered)),15)
        self.assertEqual(pipeline.parse_questions('```json\n{"questions":[{"question":"What is Hibernate?"}]}\n```'),['What is Hibernate?'])

    def test_reject_duplicate_plan(self):
        client=SimpleNamespace(chat=lambda **kw:SimpleNamespace(message=SimpleNamespace(content=json.dumps({'questions':['Same question repeated?']*15}))))
        with self.assertRaises(ValueError): pipeline.topic_questions(client,'mock','Java')

if __name__=='__main__': unittest.main()
