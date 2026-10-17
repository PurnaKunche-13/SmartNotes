"""Generate exam answers with thinking disabled and bounded CPU work."""
import logging
import re
from ollama import Client
from answer_guard import clean_answer, SYSTEM_PROMPT

log = logging.getLogger(__name__)
WORD_TARGETS = {2: (35, 65), 5: (105, 155), 10: (200, 300)}
TOKEN_LIMITS = {2: 320, 5: 700, 10: 1400}


def _words(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def generate_complete_answer(client: Client, model: str, question: str, marks: int, context: str = ''):
    if marks not in WORD_TARGETS:
        raise ValueError('Marks must be 2, 5 or 10')
    low, high = WORD_TARGETS[marks]
    prompt = (
        f'/nothink\nWrite the FINAL answer to this {marks}-mark exam question.\n'
        f'Question: {question}\n'
        f'Syllabus context: {context[:350] if context else "Not supplied"}\n'
        f'Write about {low}-{high} words in simple English. Include numbered headings, '
        'clear explanations, and a short conclusion. Answer every part of the question. '
        'Start immediately with the subject, not an explanation of your task. '
        'Never include analysis, planning, or self-talk. End with a complete sentence.'
    )
    for attempt in range(2):
        response = client.chat(
            model=model,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ],
            think=False,
            keep_alive='15m',
            options={
                'num_ctx': 2048,
                'num_predict': TOKEN_LIMITS[marks] if attempt == 0 else TOKEN_LIMITS[marks] + 350,
                'temperature': 0.2,
            },
        )
        raw = response.message.content or ''
        try:
            answer = clean_answer(raw)
        except ValueError as exc:
            log.warning('Rejected reasoning output on attempt %s: %s', attempt + 1, exc)
            continue
        if not answer:
            continue
        reason = getattr(response, 'done_reason', None)
        log.info('Question %r: %d words, finish=%s, attempt=%d', question, _words(answer), reason, attempt + 1)
        # A low word count is not, by itself, evidence of truncation. Avoid
        # rejecting a concise but otherwise complete answer repeatedly.
        if reason == 'length' and not re.search(r'[.!?]["\u201d\u2019)]?\s*$', answer):
            log.warning('Answer hit output limit mid-sentence; retrying with more tokens')
            continue
        if not re.search(r'[.!?]["\u201d\u2019)]?\s*$', answer):
            log.warning('Answer lacks terminal punctuation; retrying once')
            continue
        return answer
    raise ValueError('Model did not return a complete final answer in two attempts. Try 5 marks or inspect Ollama logs.')


def generate_exam_answer(client, model, question, marks):
    return generate_complete_answer(client, model, question, marks)
