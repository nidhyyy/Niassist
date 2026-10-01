const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(setImmediate);};

test('document answer → editable suggestions → explicit confirmation → saved tasks',async()=>{
 const dom=new JSDOM(fs.readFileSync('templates/workspace.html','utf8'),{url:'http://localhost/workspace',runScripts:'outside-only'});
 const w=dom.window;const calls=[];let tasks=[];const did='a'.repeat(32);
 w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=function(){};
 w.fetch=async(url,options={})=>{
  calls.push({url,method:options.method||'GET',data:options.body?JSON.parse(options.body):null});
  if(options.method&&options.method!=='GET')assert.equal(options.headers['X-CSRF-Token'],'csrf');
  let data;
  if(url==='/api/auth/session')data={user:{email:'test@example.test'},csrf_token:'csrf'};
  else if(url==='/api/chats')data={chats:[]};
  else if(url==='/api/documents')data={documents:[{id:did,name:'Notice.txt',size:100,status:'ready'}],worker:{online:true}};
  else if(url==='/api/notifications')data={notifications:[],worker:{online:true}};
  else if(url==='/api/activity')data={events:[]};
  else if(url==='/api/documents/ask')data={supported:true,claims:[{text:'Submit your resume.',citations:[{name:'Notice.txt',page:null,quote:'Submit your resume',document_id:did,chunk_id:'chunk'}]}]};
  else if(url==='/api/tasks/suggest')data={tasks:[{title:'Submit resume',notes:'<img src=x onerror=alert(1)>',deadline_hint:'30 September',citations:[{name:'Notice.txt',quote:'Submit your resume'}]}]};
  else if(url==='/api/tasks'&&options.method==='POST'){
   const payload=JSON.parse(options.body);assert.equal(payload.confirmed,true);
   assert.ok(payload.idempotency_key);assert.equal(payload.tasks[0].title,'Submit reviewed resume');
   assert.equal(payload.tasks[0].due_at,null);assert.equal(payload.tasks[0].remind_at,null);
   tasks=payload.tasks.map(t=>({...t,id:'task1',version:1,status:'pending',reminders:[]}));data={ids:['task1']};
  }else if(url==='/api/tasks')data={tasks,worker:{online:true}};
  else throw Error('Unexpected request '+url);
  return {ok:true,status:200,json:async()=>data};
 };
 try{
  w.eval(fs.readFileSync('static/workspace.js','utf8'));await settle();
  const checkbox=w.document.querySelector('#document-list input');checkbox.checked=true;checkbox.dispatchEvent(new w.Event('change'));
  w.document.querySelector('#doc-question').value='What should I submit?';
  const form=w.document.querySelector('#question-form');form.dispatchEvent(new w.SubmitEvent('submit',{cancelable:true,submitter:form.querySelector('button')}));await settle();
  assert.match(w.document.querySelector('#document-answer').textContent,/Submit your resume/);
  w.document.querySelector('#suggest-docs').click();await settle();
  assert.equal(calls.filter(c=>c.url==='/api/tasks'&&c.method==='POST').length,0);
  assert.equal(w.document.querySelector('#review-panel').hidden,false);
  const title=w.document.querySelector('#draft-list input[type=text]');title.value='Submit reviewed resume';title.dispatchEvent(new w.Event('input',{bubbles:true}));
  assert.equal(w.document.querySelectorAll('img').length,0);
  assert.equal(w.document.querySelector('#draft-list input[type=datetime-local]').value,'');
  const review=w.document.querySelector('#review-form');review.dispatchEvent(new w.SubmitEvent('submit',{cancelable:true,submitter:w.document.querySelector('#confirm-tasks')}));await settle();
  assert.equal(calls.filter(c=>c.url==='/api/tasks'&&c.method==='POST').length,1);
  assert.equal(w.document.querySelector('#review-panel').hidden,true);
  assert.match(w.document.querySelector('#task-list').textContent,/Submit reviewed resume/);
 }finally{w.close();}
});
