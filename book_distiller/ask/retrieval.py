"""Deterministic lexical retrieval over effective structured metadata, never source text."""
from dataclasses import dataclass
import re
import unicodedata

VERSION = 'ask-lexical-v1'
TEXT_FIELDS = ('canonical_name', 'zh_name', 'aliases', 'source_terms', 'concept_terms', 'title', 'name',
               'statement', 'summary', 'description', 'mechanism', 'reasoning', 'when_to_use', 'limitations')
STOP = set('what which how why does do is are the a an of to in on and or as this that book author should while my me i it about where have has with was were can by for'.split())


def normalize(text):
    # Same normalization as Reader search; no dependency on a possibly stale Reader snapshot.
    return unicodedata.normalize('NFKC', text).lower()


def terms(text):
    text = normalize(text)
    words = {v for v in re.findall(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', text) if v not in STOP and len(v)>1}
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        words.update(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1: words.add(run)
    return words


def intent(question):
    q = normalize(question)
    for name, patterns in [
        ('meta', ('note', '备注', '笔记', '核验', 'unsupported', '哪些问题', '没有证据', '核验不通过')),
        ('source_location', ('where', '在哪里', '原文', '出处')),
        ('comparison', ('difference', 'compare', '区别', '比较')),
        ('relationship', ('relate', 'relationship', '关系', '联系')),
        ('why', ('why', '为什么')),
        ('application', ('apply', 'my company', '应用', '我的公司')),
        ('chapter', ('chapter', '章')),
        ('concept', ('what is', 'what are', '什么是', '定义')),
    ]:
        if any((re.search(r'\b'+re.escape(p)+r's?\b',q) if p.isascii() else p in q) for p in patterns): return name
    return 'fact'


def problem_question(question):
    return any(p in normalize(question) for p in ('核验', '哪些问题', '没有证据', '不受支持', '有哪些错误', 'unsupported', 'contradicted', 'verification issue', 'failed verification'))


def quality_rank(assessment):
    if not assessment: return 2
    verdict, strength = assessment['fidelity_verdict'], assessment['evidence_strength']
    if verdict == 'supported': return {'strong': 0, 'moderate': 1, 'weak': 3, 'insufficient': 4}[strength]
    return 2 if verdict == 'partially_supported' else 5


@dataclass(frozen=True)
class AskCandidateSet:
    intent: str
    ranked_ids: list[str]
    scores: dict
    excluded_count: int


class AskRetriever:
    def __init__(self, objects, assessments):
        self.objects, self.assessments = objects, assessments

    def retrieve(self, question):
        q, qt = normalize(question), terms(question)
        rows, excluded = [], 0
        chapter_match = re.search(r'(?:chapter\s*|第\s*)(\d+)', q)
        requested_chapter = f'ch_{int(chapter_match[1]):04d}' if chapter_match else None
        problems = problem_question(question)
        for key, obj in self.objects.items():
            assessment = self.assessments.get(key)
            if assessment and quality_rank(assessment) == 5 and not problems:
                excluded += 1; continue
            value = obj['value']
            names = [value.get(f, '') for f in ('canonical_name', 'zh_name', 'title', 'name')] + value.get('aliases', [])
            exact = int(any(normalize(n) in q for n in names if n and len(n.strip()) > 1) or key.lower() in q)
            body = ' '.join(str(value.get(f, '')) for f in TEXT_FIELDS)
            overlap = len(qt & terms(body))
            score = overlap * 3 + exact * 30
            score += 30 if requested_chapter and obj['ref'].get('chapter_id') == requested_chapter else 0
            if problems and assessment and quality_rank(assessment) >= 2: score += 15
            if score: rows.append((key, exact, score, quality_rank(assessment)))
        rows.sort(key=lambda r: (-r[1], r[3], -r[2], r[0]))
        return AskCandidateSet(intent(question), [r[0] for r in rows], {r[0]:r[2] for r in rows}, excluded)

    def expand(self, root, *, allow_unsupported=False, max_objects=48):
        """Complete lower closure or explicit rejection of an oversized/ineligible chain."""
        found, active = set(), set()
        def visit(key):
            if key in found: return True
            if key in active or key not in self.objects or len(found) + len(active) >= max_objects: return False
            if not allow_unsupported and quality_rank(self.assessments.get(key)) == 5: return False
            active.add(key)
            for child in self.objects[key]['lower_ids']:
                if not visit(child): return False
            active.remove(key); found.add(key)
            return True
        return sorted(found) if visit(root) else []
