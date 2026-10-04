"""Transparent counts, formulas and configured gates; never invent semantic scores."""
from collections import Counter
import unicodedata
from book_distiller.pipeline.canonical import json_hash
from book_distiller.core.models.verification import QualityReport


def normalized(text):return unicodedata.normalize('NFC',' '.join(text.split())).casefold()

def metric(n,d,formula):return {'numerator':n,'denominator':d,'value':n/d if d else None,'formula':formula}
def count(n,formula):return {'numerator':n,'denominator':None,'value':n,'formula':formula}

def major(obj,rules):
    return obj['ref']['object_type'] not in {'atomic_claim','knowledge_atom'} or obj['value'].get('importance',0)>=rules['major_importance']

def assessment_issues(obj,assessment):
    ref=obj['ref'];verdict=assessment['fidelity_verdict'];strength=assessment['evidence_strength'];codes=assessment['reason_codes']
    result=[]
    def add(kind,severity):
        result.append({'issue_id':'issue_'+json_hash({'object':ref,'type':kind})[:24],'issue_type':kind,'severity':severity,
            'object_refs':[ref],'source_refs':assessment['citation_ids'],'chapter_id':ref.get('chapter_id'),
            'summary':assessment['verification_summary'],'suggested_action': 'rerun_claim_extraction' if ref['object_type']=='atomic_claim' else 'rerun_chapter_atoms' if ref['object_type']=='knowledge_atom' else 'rerun_book_synthesis',
            'reviewer_confidence':assessment['reviewer_confidence'],'auto_recheck_attempted':assessment['auto_recheck_attempted'],'status':'open','task_id':assessment['task_id']})
    if verdict in {'unsupported','contradicted'}:add('CONTRADICTED_CLAIM' if verdict=='contradicted' else 'UNSUPPORTED_CLAIM','major')
    if 'overclaim' in codes or verdict=='partially_supported':add('OVERCLAIM','major')
    if 'synthesis_overreach' in codes:add('SYNTHESIS_OVERREACH','major')
    if strength in {'weak','insufficient'}:add('WEAK_EVIDENCE','major')
    if assessment['lower_objects_reviewed']<assessment['lower_objects_available'] or not assessment.get('source_selection_complete',True):add('LOW_TRACEABILITY','major')
    return result


def build_report(objects,assessments,citations,coverage,issues,parse,chapter_ids,rules,provenance):
    assessed={a['object_ref']['object_id']:a for a in assessments}
    claims=[o for o in objects if o['ref']['object_type']=='atomic_claim'];atoms=[o for o in objects if o['ref']['object_type']=='knowledge_atom']
    ideas=[o for o in objects if o['ref']['object_type']=='core_idea'];majors=[o for o in objects if major(o,rules)]
    def verdict(o):return assessed.get(o['ref']['object_id'],{}).get('fidelity_verdict')
    def has_evidence(o):return bool(assessed.get(o['ref']['object_id'],{}).get('citation_ids'))
    counts=Counter(a['fidelity_verdict'] for a in assessments);major_counts=Counter(verdict(o) for o in majors)
    verdicts=('supported','partially_supported','unsupported','contradicted')
    duplicates=0;duplicate_groups=[]
    for kind,rows,field in [('atomic_claim',claims,'statement'),('knowledge_atom',atoms,'summary')]:
        groups={}
        for o in rows:groups.setdefault(normalized(o['value'][field]),[]).append(o['ref'])
        for refs in groups.values():
            if len(refs)>1:duplicates+=len(refs)-1;duplicate_groups.append(refs)
    # Merge identical batch findings without guessing semantic equivalence.
    issues=list({i['issue_id']:i for i in issues}.values())
    for refs in duplicate_groups:
        issues.append({'issue_id':'issue_'+json_hash(refs)[:24],'issue_type':'DUPLICATE_KNOWLEDGE','severity':'minor','object_refs':refs[:24],'source_refs':[],
            'chapter_id':None,'summary':f'{len(refs)} exact duplicate {refs[0]["object_type"]} texts; {len(refs)-1} excess occurrences.',
            'suggested_action':'rerun_book_synthesis','reviewer_confidence':1.0,'auto_recheck_attempted':False,'status':'open','task_id':None})
    reviewed_chapters={c['chapter_id'] for c in coverage if c['complete']}
    omissions=[i for i in issues if i['issue_type']=='MAJOR_OMISSION' and i['status']=='open']
    omission_chapters={i['chapter_id'] for i in omissions}&reviewed_chapters
    covered_chapters={o['ref']['chapter_id'] for o in claims}&{o['ref']['chapter_id'] for o in atoms}
    unsupported=sum(verdict(o) in {'unsupported','contradicted'} for o in majors)
    hallucinations=sum(o['value'].get('confidence',0)>=rules['high_confidence'] and verdict(o) in {'unsupported','contradicted'} for o in objects)
    trace=sum(c.get('resolved',False) for c in citations)
    m={
      'parse_completeness':metric(parse.blocks_with_source_span,parse.total_source_relevant_blocks,'mapped normalized Blocks / normalized source-relevant Blocks (structural proxy, not source-text completeness)'),
      'chapter_knowledge_coverage':metric(len(covered_chapters),len(chapter_ids),'recognized Chapters with both Claims and Atoms / recognized Chapters; not chapter recognition accuracy'),
      'claim_evidence_reference_coverage':metric(sum(bool(o['source_blocks']) for o in claims),len(claims),'Claims with resolvable original source references / Claims'),
      'verified_claim_support_rate':metric(sum(verdict(o)=='supported' for o in claims),len(claims),'supported Claims / all Claims'),
      'core_idea_evidence_coverage':metric(sum(verdict(o)=='supported' and has_evidence(o) for o in ideas),len(ideas),'supported Ideas with citations / all Ideas'),
      'citation_traceability_rate':metric(trace,len(citations),'resolved Citations / all published Citations'),
      'unsupported_major_objects':count(unsupported,'count(major objects with unsupported or contradicted verdict)'),
      'high_confidence_hallucinations':count(hallucinations,'count(objects with original confidence >= threshold and unsupported or contradicted verdict)'),
      'exact_duplicate_rate':metric(duplicates,len(claims)+len(atoms),'excess exact duplicate Claim/Atom texts / all Claim and Atom texts'),
      'major_omission_count':count(len(omissions),'count(open major omission issues)'),
      'major_omission_chapter_rate':metric(len(omission_chapters),len(reviewed_chapters),'reviewed Chapters with a major omission / fully reviewed Chapters; NOT key-idea omission rate'),
      'review_issue_count':count(sum(i['status']=='open' for i in issues),'count(open review issues)'),
      'fidelity_rate':metric(sum(rules['verdict_weights'].get(verdict(o),0) for o in majors),len(majors),'sum(configured verdict weight for major objects) / all major objects'),
      'coverage_review_completion':metric(len(reviewed_chapters),len(chapter_ids),'fully reviewed source Chapters / recognized Chapters')}
    reasons=[];fatal=[]
    if len(assessed)!=len(objects):fatal.append('VERIFICATION_INCOMPLETE')
    if trace!=len(citations) or any(i['issue_type']=='BROKEN_CITATION' for i in issues):fatal.append('BROKEN_CITATION')
    for name in ['parse_completeness','chapter_knowledge_coverage','core_idea_evidence_coverage','citation_traceability_rate']:
        v=m[name]['value']
        if v is not None and v<rules['min_'+name]:reasons.append(name)
    for name in ['unsupported_major_objects','high_confidence_hallucinations']:
        if m[name]['value']>rules['max_'+name]:reasons.append(name)
    if (m['exact_duplicate_rate']['value'] or 0)>=rules['duplicate_rate_exclusive_max']:reasons.append('exact_duplicate_rate')
    if len(omissions)>rules['max_major_omissions'] or (m['major_omission_chapter_rate']['value'] or 0)>=rules['major_omission_chapter_rate_exclusive_max']:reasons.append('major_omission')
    if len(reviewed_chapters)!=len(chapter_ids):reasons.append('coverage_review_incomplete')
    if any(i['status']=='open' and i['severity'] in {'major','critical'} for i in issues):reasons.append('open_major_issues')
    if parse.status!='pass':reasons.append('parse_warning')
    if not claims or not citations:reasons.append('insufficient_knowledge')
    report={'schema_version':'1.0','status':'failed' if fatal else 'needs_review' if reasons else 'pass',
        'metrics':m,'thresholds':rules,'gate_reasons':fatal+reasons,'verdict_counts':{v:counts[v] for v in verdicts},'major_verdict_counts':{v:major_counts[v] for v in verdicts},
        'major_objects_reviewed':sum(verdict(o) is not None for o in majors),'major_objects_total':len(majors),
        'issue_counts':dict(Counter(i['issue_type'] for i in issues)),
        'automatic_rechecks':sum(a['auto_recheck_attempted'] for a in assessments),
        'remaining_review_items':[i['issue_id'] for i in issues if i['status']=='open'],'provenance':provenance}
    return QualityReport.model_validate(report).model_dump(mode='json'),issues


def render_report(report):
    """Human-readable view of the same canonical values, with no hidden scores."""
    lines=['# Local Evidence Quality Report','',f"Gate Status: {report['status']}",'',
           '## Metrics','', '| Metric | Numerator | Denominator | Value | Formula |',
           '| --- | ---: | ---: | ---: | --- |']
    for name,m in report['metrics'].items():
        lines.append(f"| {name} | {m['numerator']} | {m['denominator'] if m['denominator'] is not None else 'N/A (count)'} | {m['value']} | {m['formula']} |")
    for heading,key in [('Thresholds','thresholds'),('Verdict Counts','verdict_counts'),('Major Verdict Counts','major_verdict_counts'),('Issue Counts','issue_counts')]:
        lines.extend(['',f'## {heading}',''])
        lines.extend(f'- {name}: {value}' for name,value in report[key].items())
        if not report[key]:lines.append('None.')
    lines.extend(['','## Automatic Rechecks','',str(report['automatic_rechecks']),
                  '', '## Remaining Review Items',''])
    lines.extend('- '+item for item in report['remaining_review_items'])
    if not report['remaining_review_items']:lines.append('None.')
    lines.extend(['','## Gate Reasons','',', '.join(report['gate_reasons']) or 'All configured gates satisfied.',
        '',f"Major objects reviewed: {report['major_objects_reviewed']} / {report['major_objects_total']}",
        '', 'This report evaluates local source support, not external-world truth. Omission findings are AI-detected candidates, not a gold-set omission rate.',''])
    return '\n'.join(lines)
