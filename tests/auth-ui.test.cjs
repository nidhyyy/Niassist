const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('account UI signs in, imports previous chats, and clears chat data on logout', async () => {
    const elements = new Map();
    const element = () => ({value:'', hidden:false, children:[], handlers:{}, classList:{add(){}},
        addEventListener(name, callback){this.handlers[name]=callback},
        appendChild(child){this.children.push(child)}, replaceChildren(){this.children=[]}, reportValidity(){return true}});
    const ids = ['command','chatBox','chat-select','new-chat','delete-chat','logout-button','import-chats',
                 'auth-panel','auth-form','auth-email','auth-password','login-button','register-button','auth-message','account-email'];
    ids.forEach(id=>elements.set(id,element()));
    let user = null, csrf = 'first', guest = 1, imported = false;
    const calls=[];
    const context = vm.createContext({document:{getElementById:id=>elements.get(id)||null, createElement:element},
        window:{confirm:()=>true}, AbortController, TextDecoder, setTimeout, clearTimeout,
        fetch:async (url, options={})=>{
            calls.push(url);
            if (options.method === 'POST') assert.equal(options.headers['X-CSRF-Token'],csrf);
            let data;
            if(url==='/api/auth/session') data={user,csrf_token:csrf,guest_chats:guest};
            else if(url==='/api/auth/register') {user={email:'nidhi@example.test'};csrf='rotated';data={user,csrf_token:csrf};}
            else if(url==='/api/auth/import') {imported=true;guest=0;data={imported:1};}
            else if(url==='/api/auth/logout') {user=null;csrf='logout';data={ok:true};}
            else if(url==='/api/chats') data={chats:[{id:'chat1',title:'Saved chat'}]};
            else if(url==='/api/chats/chat1') data={turns:[{question:'REST?',answer:'Saved answer'}]};
            else throw Error('Unexpected URL '+url);
            return {ok:true,json:async()=>data};
        }});
    vm.runInContext(fs.readFileSync('static/chat.js','utf8'),context);
    await new Promise(setImmediate);
    assert.equal(elements.get('auth-panel').hidden,false);
    elements.get('auth-email').value='nidhi@example.test';
    elements.get('auth-password').value='Long-password-123';
    await context.submitAccount('register');
    assert.equal(elements.get('auth-panel').hidden,true);
    assert.equal(elements.get('auth-password').value,'');
    assert.equal(elements.get('chatBox').children[1].innerText,'Saved answer');
    await elements.get('import-chats').handlers.click();
    assert.equal(imported,true);
    assert.equal(elements.get('import-chats').hidden,true);
    await elements.get('logout-button').handlers.click();
    assert.equal(elements.get('auth-panel').hidden,false);
    assert.equal(elements.get('chatBox').children.length,0);
    assert.equal(elements.get('account-email').textContent,'');
});
