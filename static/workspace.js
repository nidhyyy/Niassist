'use strict';
const $ = id => document.getElementById(id);
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
let csrf = '', selected = new Set(), drafts = [], confirmationKey = null, editTask = null, loading = false, signedIn = false;
const el = (tag, text, className) => { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(className)node.className=className; return node; };
function message(text){$('status').textContent=text;}
function workerStatus(worker){$('worker-state').textContent=worker.online ? '● Document processing & reminders active' : '● Processing and reminder delivery paused — start the worker terminal';}
function localTime(iso){return iso ? new Date(iso).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}) : 'No deadline';}
function localInput(iso){if(!iso)return '';const date=new Date(iso);return new Date(date-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function isoInput(value){if(!value)return null; const date=new Date(value); if(Number.isNaN(date.getTime()))throw Error('Choose a valid date and time.'); return date.toISOString();}
function showTab(name){for(const tab of ['documents','tasks','inbox','activity']){$(tab+'-panel').hidden=tab!==name;document.querySelector(`[data-tab="${tab}"]`).setAttribute('aria-selected',String(tab===name));}}
async function api(path,options={}){
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),45000);
 try{
  const response=await fetch(path,{...options,signal:controller.signal,headers:{'X-CSRF-Token':csrf,...options.headers}});
  const data=await response.json();
  if(response.status===401){signedIn=false;document.querySelector('main').replaceChildren();location.replace('/');throw Error('Please sign in again.');}
  if(!response.ok)throw Error(data.error?.message||'Request failed. Please try again.');
  return data;
 }catch(error){if(error.name==='AbortError')throw Error('Request timed out. Refresh the list before retrying a save.');throw error;}
 finally{clearTimeout(timer);}
}
const jsonOptions=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function action(label,callback,css='secondary'){
 const button=el('button',label,css);button.type='button';button.addEventListener('click',async()=>{
 button.disabled=true;try{await callback();}catch(error){message(error.message);}finally{button.disabled=false;}});return button;
}
function sourceCard(ref){
 const card=el('div',undefined,'source');card.append(el('strong',ref.name+(ref.page ? ' · page '+ref.page : ' · text excerpt')),el('div','“'+ref.quote+'”'));
 if(ref.document_id)card.append(action('Open source excerpt',async()=>{
  const data=await api('/api/documents/sources/'+ref.chunk_id);
  $('source-title').textContent=data.source.name+(data.source.page?' — page '+data.source.page:'');
  $('source-text').textContent=data.source.text;$('source-dialog').showModal();
 }));return card;
}
async function loadDocuments(){
 const data=await api('/api/documents');workerStatus(data.worker);
 const ids=new Set(data.documents.map(d=>d.id));selected=new Set([...selected].filter(id=>ids.has(id)));
 $('document-list').replaceChildren();
 if(!data.documents.length)$('document-list').append(el('p','No documents yet. Upload a notice or your notes.','muted'));
 for(const doc of data.documents){
  const item=el('article',undefined,'item');const label=el('label');const check=el('input');check.type='checkbox';check.disabled=doc.status!=='ready';check.checked=selected.has(doc.id);
  check.addEventListener('change',()=>{if(check.checked){if(selected.size>=5){check.checked=false;message('Select at most five documents.');return;}selected.add(doc.id);}else selected.delete(doc.id);});
  label.append(check,document.createTextNode(' '+doc.name));item.append(label,el('span',doc.status,'badge'),el('span',' · '+Math.ceil(doc.size/1024)+' KB','muted'));
  if(doc.error)item.append(el('p',doc.error,'muted'));
  const buttons=el('div',undefined,'row');
  if(doc.status==='failed')buttons.append(action('Retry',async()=>{await api('/api/documents/'+doc.id+'/retry',{method:'POST'});await loadDocuments();}));
  buttons.append(action('Delete',async()=>{if(!confirm('Delete this document and its extracted sources? Saved tasks remain.'))return;await api('/api/documents/'+doc.id,{method:'DELETE'});selected.delete(doc.id);await loadDocuments();},'danger'));
  item.append(buttons);$('document-list').append(item);
 }
}
function requiredDocs(){if(!selected.size)throw Error('Select at least one ready document first.');return [...selected];}
function openDrafts(items,editing=null){
 if(drafts.length && !confirm('Replace your unsaved task drafts?'))return;
 editTask=editing;drafts=items;confirmationKey=crypto.randomUUID();
 $('review-title').textContent=editing?'Edit task':'Review task suggestions';$('confirm-tasks').textContent=editing?'Confirm changes':'Confirm & save selected tasks';
 $('draft-list').replaceChildren();$('review-panel').hidden=false;
 $('review-note').textContent='Review the sources and deadline hints. Exact deadlines and reminder times are your choice; nothing is scheduled until you confirm.';
 for(const draft of drafts){
  const card=el('div',undefined,'draft');draft.inputs={};
  const label=el('label');const include=el('input');include.type='checkbox';include.checked=true;draft.inputs.include=include;label.append(include,document.createTextNode('Include this task'));card.append(label);
  for(const [key,title,type] of [['title','Task title','text'],['notes','Notes','textarea']]){
   const lab=el('label',title);const input=el(type==='textarea'?'textarea':'input');input.value=draft[key]||'';input.maxLength=key==='title'?200:3000;if(type==='text')input.type='text';lab.append(input);card.append(lab);draft.inputs[key]=input;
  }
  if(draft.deadline_hint)card.append(el('p','Source deadline phrase: '+draft.deadline_hint+' — choose the exact date and time below.','muted'));
  for(const ref of draft.citations||[])card.append(sourceCard(ref));
  const grid=el('div',undefined,'draft-grid');
  for(const [key,title] of [['due_at','Deadline (optional)'],['remind_at','In-app reminder (optional)']]){
   const lab=el('label',title);const input=el('input');input.type='datetime-local';input.value=localInput(draft[key]);lab.append(input);grid.append(lab);draft.inputs[key]=input;
  }card.append(grid);card.addEventListener('input',()=>{confirmationKey=crypto.randomUUID();});$('draft-list').append(card);
 }
 $('review-panel').scrollIntoView({behavior:'smooth'});
}
async function getSuggestions(fromChat){
 const payload=fromChat?{conversation_id:$('chat-source').value}:{document_ids:requiredDocs(),question:$('task-request').value};
 if(fromChat&&!payload.conversation_id)throw Error('Choose a saved conversation.');
 message('Reading source excerpts and preparing editable suggestions…');
 const data=await api('/api/tasks/suggest',jsonOptions('POST',payload));
 if(!data.tasks.length){message('No supported tasks found in the retrieved excerpts. You can add a task manually.');return;}
 openDrafts(data.tasks);message('Suggestions are drafts. Check them against the sources before confirming; long documents may not be fully covered.');
}
async function loadTasks(){
 const data=await api('/api/tasks');workerStatus(data.worker);$('task-list').replaceChildren();
 const filter=$('task-filter').value;const tasks=data.tasks.filter(t=>filter==='all'||t.status===filter);
 if(!tasks.length)$('task-list').append(el('p','No tasks in this view. Create a checklist or add a task.','muted'));
 for(const task of tasks){
  const item=el('article',undefined,'item');item.append(el('h3',task.title),el('span',task.status,'badge'),el('p',task.notes));
  item.append(el('p','Deadline: '+localTime(task.due_at)+' · saved zone: '+task.timezone,'muted'));
  if(task.source)item.append(el('p','Source: '+task.source,'muted'));
  const latest=task.reminders[0];if(latest)item.append(el('p','Reminder: '+localTime(latest.scheduled_at)+' · '+latest.status+(latest.status==='delivered'?' to inbox':''),'muted'));
  const buttons=el('div',undefined,'row');
  buttons.append(action(task.status==='pending'?'Mark complete':'Reopen',async()=>{await api('/api/tasks/'+task.id,jsonOptions('PATCH',{version:task.version,status:task.status==='pending'?'completed':'pending'}));await refreshTasks();}));
  buttons.append(action('Edit',()=>openDrafts([{...task,remind_at:latest?.status==='scheduled'?latest.scheduled_at:null}],task)));
  buttons.append(action('Delete',async()=>{if(!confirm('Delete this task and its reminders?'))return;await api('/api/tasks/'+task.id,{method:'DELETE'});await refreshTasks();},'danger'));item.append(buttons);$('task-list').append(item);
 }
}
async function loadInbox(){
 const data=await api('/api/notifications');workerStatus(data.worker);$('notification-list').replaceChildren();
 const unread=data.notifications.filter(n=>!n.read_at).length;$('unread-count').textContent=unread?'('+unread+')':'';
 if(!data.notifications.length)$('notification-list').append(el('p','No reminders delivered yet.','muted'));
 for(const note of data.notifications){const item=el('article',undefined,'item'+(note.read_at?'':' unread'));item.append(el('h3',note.title),el('p','Delivered to inbox '+new Date(note.created*1000).toLocaleString(),'muted'));
 if(!note.read_at)item.append(action('Mark read',async()=>{await api('/api/notifications/'+note.id+'/read',{method:'POST'});await loadInbox();}));$('notification-list').append(item);}
}
async function loadActivity(){const data=await api('/api/activity');$('activity-list').replaceChildren();for(const event of data.events){const item=el('div',undefined,'item');item.append(el('strong',event.action.replaceAll('_',' ')),el('div',event.title),el('small',new Date(event.created*1000).toLocaleString()));$('activity-list').append(item);}if(!data.events.length)$('activity-list').append(el('p','Your actions will appear here.','muted'));}
async function refreshTasks(){await Promise.all([loadTasks(),loadInbox(),loadActivity()]);}
async function busy(button,work){button.disabled=true;try{await work();}catch(error){message(error.message);}finally{button.disabled=false;}}
$('zone-label').textContent='Your time zone: '+zone;$('review-zone').textContent='Enter times in '+zone+'. Dates are stored in UTC. Reminders require the worker to be running.';
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>showTab(button.dataset.tab)));
$('upload-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const file=$('upload-file').files[0];if(!file||file.size>5*1024*1024)throw Error('Choose a file up to 5 MB.');const data=new FormData();data.append('file',file);await api('/api/documents',{method:'POST',body:data});$('upload-form').reset();message('Upload queued. The worker will extract the text.');await loadDocuments();});});
$('question-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{message('Finding evidence…');$('document-answer').replaceChildren();const data=await api('/api/documents/ask',jsonOptions('POST',{question:$('doc-question').value,document_ids:requiredDocs()}));$('document-answer').replaceChildren();if(!data.supported)$('document-answer').append(el('p',data.message));for(const claim of data.claims){const block=el('article',undefined,'item');block.append(el('p',claim.text));claim.citations.forEach(ref=>block.append(sourceCard(ref)));$('document-answer').append(block);}message('Source quotes were checked against the stored text. Review them to verify the answer.');});});
$('suggest-docs').addEventListener('click',()=>busy($('suggest-docs'),()=>getSuggestions(false)));
$('suggest-chat').addEventListener('click',()=>busy($('suggest-chat'),()=>getSuggestions(true)));
$('new-task').addEventListener('click',()=>openDrafts([{title:'',notes:''}]));
$('cancel-review').addEventListener('click',()=>{drafts=[];editTask=null;$('review-panel').hidden=true;$('draft-list').replaceChildren();});
$('review-form').addEventListener('submit',event=>{event.preventDefault();busy($('confirm-tasks'),async()=>{
 const chosen=drafts.filter(d=>d.inputs.include.checked).map(d=>({title:d.inputs.title.value.trim(),notes:d.inputs.notes.value,
 due_at:isoInput(d.inputs.due_at.value),remind_at:isoInput(d.inputs.remind_at.value),timezone:zone,
 source:d.source||(d.citations||[]).map(c=>c.name+(c.page?' p.'+c.page:'')).join('; ').slice(0,500)}));
 if(!chosen.length)throw Error('Select at least one task.');if(chosen.some(t=>!t.title))throw Error('Every selected task needs a title.');
 if(editTask)await api('/api/tasks/'+editTask.id,jsonOptions('PATCH',{...chosen[0],version:editTask.version,status:editTask.status}));
 else await api('/api/tasks',jsonOptions('POST',{confirmed:true,idempotency_key:confirmationKey,tasks:chosen}));
 drafts=[];editTask=null;$('review-panel').hidden=true;$('draft-list').replaceChildren();showTab('tasks');message('Tasks saved. Only the reminder times you confirmed have been scheduled.');await refreshTasks();
});});
$('task-filter').addEventListener('change',()=>loadTasks().catch(e=>message(e.message)));
$('refresh-tasks').addEventListener('click',()=>busy($('refresh-tasks'),refreshTasks));
async function initialize(){try{const session=await api('/api/auth/session');if(!session.user){location.replace('/');return;}csrf=session.csrf_token;signedIn=true;$('user-email').textContent=session.user.email;
 const chats=await api('/api/chats');for(const chat of chats.chats){const option=el('option',chat.title);option.value=chat.id;$('chat-source').append(option);}
 const requested=new URLSearchParams(location.search).get('chat');if(requested&&chats.chats.some(c=>c.id===requested)){$('chat-source').value=requested;showTab('tasks');}
 await Promise.all([loadDocuments(),refreshTasks()]);
 }catch(error){message(error.message);}}
initialize();
setInterval(async()=>{if(!signedIn||document.hidden||loading)return;loading=true;try{await Promise.all([loadDocuments(),loadInbox()]);}catch(error){message(error.message);}finally{loading=false;}},10000);
