"""Ensure only the final answer is passed to the handwriting renderer."""
import re

SYSTEM_PROMPT = (
    'You are writing the FINAL ANSWER for a university exam. '
    'Do not output planning, analysis, self-talk, instructions, or reasoning. '
    'Start directly with the answer, using numbered points.'
)

_THINK_BLOCK = re.compile(r'<think\b[^>]*>.*?</think\s*>', re.I | re.S)
_THINK_OPEN = re.compile(r'<think\b[^>]*>.*$', re.I | re.S)
_SELF_TALK = re.compile(
    r'(?im)^\s*(?:hmm[, .]|i should\b|i need to\b|i(?:\x27ll| will)\s+(?:structure|draft|start|avoid|cover|make|count)\b|'
    r'the user wants\b|the user (?:might|specified|needs)\b|now,? drafting\b|'
    r'word count is crucial\b|for a university exam question,? i need\b)'
)


def clean_answer(content):
    text = _THINK_BLOCK.sub('', content or '')
    text = _THINK_OPEN.sub('', text).strip()
    if '</think>' in text.lower():
        text = re.split(r'</think\s*>', text, flags=re.I)[-1].strip()
    text = re.sub(r'^```(?:markdown|text)?\s*|\s*```$', '', text, flags=re.I).strip()
    if not text or _SELF_TALK.search(text):
        raise ValueError('Model returned reasoning instead of a final answer; try again.')
    return text
