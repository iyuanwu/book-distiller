/* Local, text-only rendering. No fetch, network, storage writes or HTML injection. */
(() => {
'use strict';
const D=window.BOOK_DISTILLER_DATA, O=D.knowledge.objects, A=D.quality.assessments, C=D.concepts;
const main=document.getElementById('main'), panel=document.getElementById('evidence-panel');
const names={core_idea:'Core Ideas · 核心思想',mental_model:'Mental Models · 思维模型',meta_principle:'Meta Principles · 元原则',knowledge_atom:'Knowledge Atoms · 知识原子',atomic_claim:'Atomic Claims · 原子主张'};
const levelNames=['一句话理解','快速阅读 · 约 3 分钟','结构阅读 · 约 15 分钟','深入阅读 · 约 60 分钟','完整知识模型','原文与证据'];
const E=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const link=(text,route)=>{const a=E('a',text);a.href='#'+encodeURIComponent(route);return a;};
const button=(text,fn)=>{const b=E('button',text);b.addEventListener('click',fn);return b;};
const title=o=>o.title||o.name||o.statement||o.id;
const textFields=['statement','summary','description','mechanism','when_to_use','limitations','examples','reasoning','promotion_reason','concept_terms'];
const anchors=new Map(Object.values(O).map(o=>[o.anchor,o.id]));
const chapterName=id=>D.book.chapters[id]?.title||id;
function objectLink(id){const o=O[id];return o?link(title(o).slice(0,120),o.anchor):E('span','Unavailable object: '+id);}
function linked(parent,ids){const box=E('div',undefined,'links');for(const id of ids)box.append(objectLink(id));parent.append(box);}
function heading(kicker,name,description){main.append(E('div',kicker,'eyebrow'),E('h1',name));if(description)main.append(E('p',description,'intro'));}
function badge(a){return E('span',a?.fidelity_verdict||'unavailable','badge '+(a?.fidelity_verdict||''));}
function objectIssues(id){return D.quality.issues.filter(i=>i.object_refs.some(r=>r.object_id===id));}
function issueView(i){const e=E('article');e.append(E('span',i.severity+' · '+i.issue_type,'badge'),E('p',i.summary));if(i.suggested_action)e.append(E('p',i.suggested_action,'muted'));linked(e,i.object_refs.map(r=>r.object_id));for(const id of i.source_refs)e.append(link('Citation '+id.slice(-8),'citation-'+id));return e;}
function quality(target){target.append(E('div','QUALITY & PROVENANCE','eyebrow'),E('h2','质量与边界'),E('strong',D.quality.report.status.toUpperCase()));target.append(E('p','PASS 表示通过本地原书证据规则，不代表外部事实已验证。','muted'));if(D.quality.report.status==='needs_review')target.append(E('p','This knowledge model requires review.','notice'));
 const metrics=E('div',undefined,'metrics');for(const [name,m] of Object.entries(D.quality.report.metrics)){const row=E('div',undefined,'metric');row.append(E('span',name.replaceAll('_',' ')),E('strong',`${m.numerator ?? '—'} / ${m.denominator ?? '—'}${m.value===null?'':` · ${Number(m.value).toFixed(3)}`}`));row.title=m.formula;metrics.append(row);}target.append(metrics,link(`Quality Issues (${D.quality.issues.length})`,'quality'),E('p','📖 Source · 原书证据\n🤖 AI · 知识提炼与核验\n🌐 External · 当前无\n✍️ User · 当前无','muted'));const md=E('a','完整 quality-report.md');md.href='markdown/quality-report.md';target.append(md);
}
function sourceLocation(c){return c.page_number!==null?`PDF Page ${c.page_number}（物理页） · ${c.block_id}`:`${chapterName(c.chapter_id)} · ${c.section_id||'无 Section'} · ${c.block_id}`;}
function sourceCard(c,full=false){const box=E('div',undefined,'source');box.append(E('span','📖 SOURCE · 原书片段','badge'),E('p',sourceLocation(c),'meta'));const preview=c.excerpt.slice(0,400);box.append(E('p',preview||'显示预算已用完；请按 Source location 查看原件。'));
 if(c.excerpt.length>400){const detail=E('details');detail.append(E('summary','展开片段（最多 800 字符）'),E('p',c.excerpt.slice(400)));box.append(detail);}if(c.excerpt_truncated)box.append(E('p',`显示 ${c.excerpt_chars} / ${c.range_chars} 字符；完整范围见原件。`,'meta'));
 box.append(E('p',`Excerpt derived from canonical source · chars [${c.char_start}, ${c.char_end})`,'meta'));
 if(full){const detail=E('details');detail.append(E('summary','SourceSpan / parser location'),E('pre',JSON.stringify({parser:c.parser,locator:c.parser_locator,spans:c.source_spans},null,2)));box.append(detail);if(c.context.length){const context=E('details');context.append(E('summary','前后文（局部、默认折叠）'));for(const b of c.context)context.append(E('p',b.block_id,'meta'),E('p',b.text+(b.truncated?' …':'')));box.append(context);}}
 else box.append(link('查看 Citation 与 SourceSpan →','citation-'+c.citation_id));
 return box;
}
function original(target,c){const s=D.book.source;target.append(E('p',s.path,'meta'));if(s.available){const a=E('a','Open Original ↗');a.href=s.uri+(c?.page_number&&s.path.toLowerCase().endsWith('.pdf')?'#page='+c.page_number:'');target.append(a);}else target.append(E('p','Open Original unavailable · 原文件缺失；已有 Canonical 证据仍可读取。','notice'));}
function evidence(id){panel.replaceChildren();const o=O[id],a=A[id];panel.append(E('div','OBJECT VERIFICATION','eyebrow'),E('h2',title(o)),badge(a),E('p','Evidence strength: '+a.evidence_strength),E('p',`Original AI confidence: ${o.confidence ?? '—'}\nReviewer confidence: ${a.reviewer_confidence}`,'muted'),E('p',a.verification_summary));if(a.unsupported_aspects.length)panel.append(E('p',a.unsupported_aspects.join('\n'),'notice'));
 const chapters=o.supporting_chapters||(o.chapter_id?[o.chapter_id]:[]);if(chapters.length){panel.append(E('h3','Supporting chapters'));for(const ch of chapters)panel.append(link(chapterName(ch),'chapter-'+ch),E('br'));}
 if(o.lower_ids.length){panel.append(E('h3','Supporting objects'));linked(panel,o.lower_ids);}panel.append(E('h3','Citations'));pageList(panel,a.citation_ids,cid=>{const card=sourceCard(D.evidence.entries[cid]);if(a.supplemental_citation_ids.includes(cid))card.prepend(E('span','Supplemental · 局部复核补充','badge'));return card;},3);if(!a.citation_ids.length)panel.append(E('p','此对象无直接 Citation；查看下层证据。','notice'));
 for(const issue of objectIssues(id))panel.append(issueView(issue));const binding=E('details');binding.append(E('summary','核验与 generation 绑定'),E('pre',JSON.stringify({assessment_id:a.assessment_id,object_ref:a.object_ref,verification_generation_id:a.verification_generation_id,original_citation_ids:a.original_citation_ids,supplemental_citation_ids:a.supplemental_citation_ids},null,2)));panel.append(binding);original(panel);}
function objectCard(id,selection=null){const o=O[id],a=A[id],article=E('article');article.id=o.anchor;article.append(E('span','🤖 AI · '+o.type.replaceAll('_',' '),'badge'),badge(a),E('h2',selection?.title||title(o)));
 if(selection){for(const f of selection.fields)article.append(E('p',f.field.replaceAll('_',' '),'field-label'),E('p',f.text));}
 else for(const field of textFields){const value=o[field];if(value&&(!Array.isArray(value)||value.length)){article.append(E('p',field.replaceAll('_',' '),'field-label'),E('p',Array.isArray(value)?value.join('\n'):value));}}
 if(!selection)article.append(E('p',`${o.claim_type||o.atom_type||o.type} · Importance ${o.importance??'—'} · Original AI confidence ${o.confidence??'—'}`,'meta'));
 const actions=E('div',undefined,'object-actions');actions.append(link('打开对象 →',o.anchor),button('证据与核验',()=>{evidence(id);document.getElementById('right').classList.add('open');}));article.append(actions);const issues=objectIssues(id).filter(i=>i.status==='open');if(issues.length)article.append(E('p',`${issues.length} open quality issue(s) · ${issues.map(i=>i.issue_type).join(', ')}`,'notice'));return article;}
function pageList(target,ids,render,pageSize=40){let pos=0;const more=button('继续显示',()=>append());const append=()=>{more.remove();for(const id of ids.slice(pos,pos+pageSize))target.append(render(id));pos+=pageSize;if(pos<ids.length){more.textContent=`继续显示 · ${Math.min(pos,ids.length)} / ${ids.length}`;target.append(more);}};append();}
function chapter(id){const ch=D.book.chapters[id];heading('CHAPTER',ch.title,`${ch.atom_ids.length} Atoms · ${ch.claim_ids.length} Claims`);main.append(E('h2','章节贡献'));linked(main,Object.values(O).filter(o=>(o.supporting_chapters||[]).includes(id)).map(o=>o.id));pageList(main,ch.atom_ids,k=>objectCard(k));const claims=E('details');claims.append(E('summary',`Atomic Claims (${ch.claim_ids.length})`));pageList(claims,ch.claim_ids,k=>objectCard(k));main.append(claims);main.append(E('h2','Coverage review'));const coverage=D.quality.coverage;const rows=coverage.reviews||coverage.batches||[];const selected=rows.filter(r=>r.chapter_id===id);if(selected.length)for(const row of selected)main.append(E('p',row.review_summary||JSON.stringify(row)));else main.append(E('p','Coverage 数据见完整质量记录。','muted'));for(const issue of D.quality.issues.filter(i=>i.chapter_id===id))main.append(issueView(issue));}
function concept(id){const c=C[id];heading('CONCEPT',c.canonical_name,c.zh_name);main.append(E('p','Aliases: '+c.aliases.join(' · ')));for(const r of c.relations){const p=E('p');p.append(link(C[r.target_concept_id].canonical_name,'concept-'+r.target_concept_id),E('span',' · '+r.relation+' · '+r.reason));main.append(p);}main.append(E('h2','Core Ideas'));linked(main,Object.values(O).filter(o=>o.type==='core_idea'&&(o.concept_ids||[]).includes(id)).map(o=>o.id));main.append(E('h2','Atoms'));pageList(main,c.atom_ids,k=>objectCard(k));const claims=E('details');claims.append(E('summary',`Claims (${c.claim_ids.length})`));pageList(claims,c.claim_ids,k=>objectCard(k));main.append(claims);}
function level(n){main.className='l'+n;heading('PROGRESSIVE READING · L'+n,levelNames[n],n<4?'由已核验知识对象确定性选取。阅读时间仅作方向提示，实际长度取决于原书与模型。':n===4?'保留所有知识对象与质量状态；按组浏览，逐步展开。':'Knowledge Object → Verification → Citation → SourceSpan');
 if(n<4){const v=D.progressive['l'+n];main.append(E('p',`${v.chars.toLocaleString()} 字符 · ${v.items.length} 个对象 · 其余见 L4`,'meta'));if(n===1)main.append(E('p','Classification: '+D.book.classification.primary_type,'meta'));for(const item of v.items)main.append(objectCard(item.id,item));if(!v.items.length)main.append(E('p','没有符合当前层级证据与长度规则的对象。请查看 L4。','empty'));if(n===2){main.append(E('h2','概念结构'));const concepts=E('div',undefined,'links');for(const id of v.concept_ids)concepts.append(link(C[id].canonical_name,'concept-'+id));main.append(concepts,E('h2','章节贡献'));for(const contribution of v.chapters){const ch=contribution.chapter_id;main.append(link(chapterName(ch),'chapter-'+ch));linked(main,contribution.object_ids);}}if(n===3)main.append(E('p','选取有界，完整 Claims / Atoms 保留在 L4；本层不替代原书。','notice'));}
 else if(n===4){for(const [kind,name] of Object.entries(names)){const count=Object.values(O).filter(o=>o.type===kind).length;main.append(E('h2'),link(`${name} (${count})`,'group-'+kind));}main.append(E('p'),link(`Concepts (${Object.keys(C).length})`,'concepts'),E('p'),link(`Relationships (${D.knowledge.relationships.length})`,'relationships'));}
 else {main.append(E('p','📖 Source = 原书证据；🤖 AI = 提炼与核验；🌐 External / ✍️ User 当前无。','notice'));pageList(main,Object.keys(D.evidence.entries),id=>sourceCard(D.evidence.entries[id],true),12);original(main);}}
function mindmap(){heading('KNOWLEDGE MAP','知识地图','Principle → Model → Idea → Concept；只展示高层结构。');const {nodes,roots}=D.mindmap;function tree(id,seen=new Set()){const n=nodes[id],box=E('details',undefined,'tree');box.open=seen.size<1;const summary=E('summary');summary.append(link(n.title,n.anchor));box.append(summary);if(!seen.has(id)){const next=new Set([...seen,id]);for(const child of n.children)box.append(tree(child,next));}return box;}for(const id of roots)main.append(tree(id));}
function search(query){heading('LOCAL SEARCH','搜索知识模型',query?`“${query}”`:'输入概念、别名、观点或 Claim。');const norm=query.normalize('NFKC').toLowerCase();const results=norm?D.search.filter(s=>s.text.includes(norm)):[];main.append(E('p',results.length+' 个结果','meta'));pageList(main,results,s=>{const row=E('div',undefined,'result');row.append(E('span',s.type,'badge'),E('h3'));row.lastChild.append(link(s.title,s.anchor));row.append(E('p',s.preview,'muted'));return row;});}
function resolveRoute(rawHash){
 let hash;try{hash=decodeURIComponent(rawHash.replace(/^#/,''));}catch{return {hash:'l0',fallback:true};}
 if(!hash)return {hash:'l0',fallback:false};
 const known=/^l[0-5]$/.test(hash)||anchors.has(hash)||['concepts','quality','map','cards','relationships'].includes(hash)||hash.startsWith('search:')||
  (hash.startsWith('group-')&&Object.hasOwn(names,hash.slice(6)))||
  (hash.startsWith('chapter-')&&Object.hasOwn(D.book.chapters,hash.slice(8)))||
  (hash.startsWith('concept-')&&Object.hasOwn(C,hash.slice(8)))||
  (hash.startsWith('citation-')&&Object.hasOwn(D.evidence.entries,hash.slice(9)));
 return known?{hash,fallback:false}:{hash:'l0',fallback:true};
}
function route(){const resolved=resolveRoute(window.location.hash),hash=resolved.hash;
 // Replace only empty/obsolete entries; never add history entries on hashchange.
 if(!window.location.hash||window.location.hash==='#'||resolved.fallback)window.history.replaceState(window.history.state,'','#l0');
 main.replaceChildren();main.className='';panel.replaceChildren();quality(panel);document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',decodeURIComponent(a.hash.slice(1))===hash));document.getElementById('left').classList.remove('open');
 if(/^l[0-5]$/.test(hash))level(Number(hash[1]));
 else if(anchors.has(hash)){const id=anchors.get(hash),o=O[id];heading('KNOWLEDGE OBJECT',o.type.replaceAll('_',' '),id);main.append(objectCard(id));main.append(E('h3','Supporting objects'));linked(main,o.lower_ids);for(const cid of o.concept_ids||[])main.append(link(C[cid].canonical_name,'concept-'+cid),E('br'));main.append(E('h3','Referenced by'));linked(main,Object.values(O).filter(v=>v.lower_ids.includes(id)).map(v=>v.id));evidence(id);}
 else if(hash.startsWith('citation-')&&D.evidence.entries[hash.slice(9)]){const c=D.evidence.entries[hash.slice(9)];heading('EVIDENCE / SOURCE',c.block_id,'Citation '+c.citation_id);main.append(sourceCard(c,true));original(main,c);main.append(E('h3','Supporting knowledge'));linked(main,Object.keys(A).filter(k=>A[k].citation_ids.includes(c.citation_id)));}
 else if(hash.startsWith('chapter-')&&D.book.chapters[hash.slice(8)])chapter(hash.slice(8));
 else if(hash.startsWith('concept-')&&C[hash.slice(8)])concept(hash.slice(8));
 else if(hash==='concepts'){heading('CONCEPT REGISTRY','概念目录');pageList(main,Object.keys(C),id=>{const p=E('p');p.append(link(C[id].canonical_name,'concept-'+id),E('span',C[id].zh_name?' · '+C[id].zh_name:'','muted'));return p;});}
 else if(hash.startsWith('group-')&&names[hash.slice(6)]){const kind=hash.slice(6);heading('FULL KNOWLEDGE MODEL',names[kind]);pageList(main,Object.keys(O).filter(k=>O[k].type===kind),k=>objectCard(k));}
 else if(hash==='quality'){heading('QUALITY REPORT','质量与复核');quality(main);for(const i of D.quality.issues)main.append(issueView(i));}
 else if(hash==='map')mindmap();
 else if(hash==='cards'){heading('KNOWLEDGE CARDS','知识卡片','既有 canonical 对象的视图；不生成新摘要。');const cards=E('div',undefined,'card-grid');pageList(cards,D.cards.object_ids,k=>{const box=objectCard(k);linked(box,O[k].lower_ids);for(const ch of O[k].supporting_chapters||[])box.append(link(chapterName(ch),'chapter-'+ch),E('br'));return box;});main.append(cards);}
 else if(hash==='relationships'){heading('RELATIONSHIPS','对象关系');pageList(main,D.knowledge.relationships,r=>{const p=E('article');p.append(E('h3',r.decision),E('p',r.reason));linked(p,r.source_atom_ids);return p;});}
 else if(hash.startsWith('search:'))search(hash.slice(7));
 if(resolved.fallback)main.append(E('p','原链接目标已不可用，已返回 L0。','notice'));
 window.scrollTo(0,0);}
const nav=document.getElementById('navigation');for(let n=0;n<6;n++)nav.append(link(`L${n} · ${levelNames[n].split(' · ')[0]}`,'l'+n));nav.append(E('div','KNOWLEDGE','nav-heading'));for(const [kind,name] of Object.entries(names))nav.append(link(name.split(' · ')[1],'group-'+kind));nav.append(link('概念目录','concepts'),link('知识地图','map'),link('知识卡片','cards'),link('质量报告','quality'),E('div','CHAPTERS','nav-heading'));for(const [id,ch]of Object.entries(D.book.chapters))nav.append(link(ch.title,'chapter-'+id));
document.getElementById('search-form').addEventListener('submit',e=>{e.preventDefault();window.location.hash=encodeURIComponent('search:'+document.getElementById('search').value.trim());});
for(const [btn,side]of [['nav-toggle','left'],['evidence-toggle','right']])document.getElementById(btn).addEventListener('click',()=>{const open=document.getElementById(side).classList.toggle('open');document.getElementById(btn).setAttribute('aria-expanded',String(open));});
window.addEventListener('hashchange',route);route();
})();
