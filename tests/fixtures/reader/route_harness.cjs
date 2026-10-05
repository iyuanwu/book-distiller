/* Unit harness only: executes the real reader script with deterministic DOM/history doubles.
   No browser, layout engine, network, or file:// navigation is used. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [script,dataFile,scenario]=process.argv.slice(2),data=JSON.parse(fs.readFileSync(dataFile,'utf8'));
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.events={};this._text='';this.className='';this.classes=new Set();this.classList={add:v=>this.classes.add(v),remove:v=>this.classes.delete(v),toggle:(v,on)=>{const yes=on??!this.classes.has(v);yes?this.classes.add(v):this.classes.delete(v);return yes;}};}
 set textContent(v){this._text=v==null?'':String(v);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 append(...nodes){for(const n of nodes){n.remove();n.parent=this;this.children.push(n);}}
 prepend(...nodes){for(const n of nodes.reverse()){n.remove();n.parent=this;this.children.unshift(n);}}
 replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
 remove(){if(this.parent){this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}}
 get lastChild(){return this.children.at(-1);}
 get hash(){return this.href?.includes('#')?'#'+this.href.split('#').slice(1).join('#'):'';}
 addEventListener(name,fn){this.events[name]=fn;}
 setAttribute(name,value){this[name]=value;}
}
const ids=Object.fromEntries(['gate','main','evidence-panel','left','right','navigation','search-form','search','nav-toggle','evidence-toggle'].map(k=>[k,new Element('div')]));
const links=()=>ids.navigation.children.filter(n=>n.tag==='a');
const document={getElementById:k=>ids[k],createElement:t=>new Element(t),querySelectorAll:q=>{assert.equal(q,'nav a');return links();}};
const initial=scenario==='valid'?'#l3':scenario==='invalid'?'#obsolete-claim':scenario==='malformed'?'#%ZZ':'';
const entries=[initial];let position=0,replaces=0;const events={};
const location={get hash(){return entries[position];},set hash(v){entries.splice(position+1);entries.push(v.startsWith('#')?v:'#'+v);position++;events.hashchange?.();}};
const history={state:{marker:'preserve me'},replaceState(state,unused,url){assert.equal(state,this.state);assert.equal(url,'#l0');entries[position]=url;replaces++;},back(){if(position>0){position--;events.hashchange();}},forward(){if(position<entries.length-1){position++;events.hashchange();}}};
const window={BOOK_DISTILLER_DATA:data,location,history,scrollTo(){},addEventListener:(name,fn)=>events[name]=fn};
if(scenario==='stale')window.BOOK_DISTILLER_LIVE_STATUS={status:'stale',reason:'Human semantic edit'};
vm.runInNewContext(fs.readFileSync(script,'utf8'),{window,document,location,history,console});
function readable(){assert.ok(ids.main.children.length>0);assert.ok(!ids.main.textContent.includes('找不到这个目标'));}
readable();
if(scenario==='empty'){assert.equal(location.hash,'#l0');assert.equal(ids.main.className,'l0');assert.equal(entries.length,1);}
if(scenario==='valid'){assert.equal(location.hash,'#l3');assert.equal(ids.main.className,'l3');assert.equal(replaces,0);}
if(scenario==='invalid'||scenario==='malformed'){assert.equal(location.hash,'#l0');assert.equal(ids.main.className,'l0');assert.ok(ids.main.textContent.includes('已返回 L0'));assert.equal(entries.length,1);}
if(scenario==='navigation'){
 const targets=links().map(n=>n.hash);
 for(const required of ['l0','l1','l2','l3','l4','l5','group-core_idea','group-mental_model','group-meta_principle','group-knowledge_atom','group-atomic_claim','concepts'])assert.ok(targets.includes('#'+required),required);
 for(const target of targets){const before=replaces;location.hash=target;readable();assert.equal(location.hash,target);assert.equal(replaces,before,'generated target must resolve without fallback: '+target);const selected=links().find(n=>n.hash===target);assert.ok(selected.classes.has('active'));
  if(/^#l[0-5]$/.test(target))assert.equal(ids.main.className,target.slice(1));
  else if(target==='#quality')assert.ok(ids.main.textContent.includes('QUALITY REPORT'));
  else assert.ok(ids.main.textContent.includes(selected.textContent),'wrong view for '+target);
 }
}
if(scenario==='history'){
 location.hash='#l1';location.hash='#l5';assert.equal(ids.main.className,'l5');
 history.back();assert.equal(location.hash,'#l1');assert.equal(ids.main.className,'l1');
 history.back();assert.equal(location.hash,'#l0');assert.equal(ids.main.className,'l0');
 history.forward();assert.equal(location.hash,'#l1');history.forward();assert.equal(ids.main.className,'l5');
 assert.deepEqual(entries,['#l0','#l1','#l5']);assert.equal(replaces,1);
 location.hash='#expired';assert.equal(location.hash,'#l0');history.back();assert.equal(location.hash,'#l5');history.forward();assert.equal(location.hash,'#l0');assert.equal(entries.length,4);
}
if(scenario==='search'){
 ids.search.value='已有';ids['search-form'].events.submit({preventDefault(){}});
 assert.equal(decodeURIComponent(location.hash),'#search:已有');assert.ok(ids.main.textContent.includes('搜索知识模型'));
}
console.log(JSON.stringify({scenario,passed:true,hash:location.hash,navigationTargets:links().map(n=>n.hash)}));

if(scenario==='stale'){assert.ok(ids.gate.textContent.includes('NEEDS RE-VERIFICATION'));assert.ok(!ids.gate.textContent.includes('PASS'));}
if(scenario==='human'){location.hash='#idea-idea';for(const text of ['Human modified','Human verified','Locked','✍️ User Note'])assert.ok(ids.main.textContent.includes(text),text);}
