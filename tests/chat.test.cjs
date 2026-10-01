const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup() {
    const messages = [];
    const input = { value: '', addEventListener() {} };
    const chat = { appendChild(el) { messages.push(el); } };
    const pending = [];
    const context = vm.createContext({
        document: {
            getElementById(id) { return id === 'command' ? input : id === 'chatBox' ? chat : null; },
            createElement() { return { classList: { add() {} } }; },
        },
        window: {}, AbortController, setTimeout, clearTimeout, TextDecoder,
        fetch() { return new Promise(resolve => pending.push(resolve)); },
    });
    vm.runInContext(fs.readFileSync('static/chat.js', 'utf8'), context);
    return { context, input, messages, pending };
}

test('followups wait until the first answer completes', async () => {
    const s = setup();
    s.input.value = 'first';
    const first = s.context.sendCommand();
    s.input.value = 'second';
    const second = s.context.sendCommand();
    await new Promise(setImmediate);
    assert.equal(s.pending.length, 1);
    assert.match(s.messages[3].innerText, /Queued/);
    await new Promise(setImmediate);
    s.pending[0](streamResponse('first answer'));
    await first;
    await new Promise(setImmediate);
    assert.equal(s.pending.length, 2);
    s.pending[1](streamResponse('second answer'));
    await second;
    assert.equal(s.messages[1].innerText, 'first answer');
    assert.equal(s.messages[3].innerText, 'second answer');
});

test('API failure shows the error message', async () => {
    const s = setup();
    s.input.value = 'hello';
    const request = s.context.sendCommand();
    await new Promise(setImmediate);
    s.pending[0]({ ok: false, json: async () => ({ error: { message: 'Please retry later.' } }) });
    await request;
    assert.equal(s.messages[1].innerText, 'Please retry later.');
});

test('unsupported voice input offers typing', () => {
    const s = setup();
    s.context.startListening();
    assert.match(s.messages[0].innerText, /type your message/);
});

function streamResponse(text, ending = {type: 'done', first_text_ms: 10, total_ms: 20}) {
    const bytes = new TextEncoder().encode(
        JSON.stringify({type: 'delta', text}) + '\n' + JSON.stringify(ending) + '\n');
    // Split every UTF-8 character and JSON record across network reads.
    return {ok: true, body: new ReadableStream({start(controller) {
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
    }})};
}

test('split UTF-8 and JSON chunks reconstruct the answer', async () => {
    const s = setup(); s.input.value = 'hi';
    const request = s.context.sendCommand();
    await new Promise(setImmediate);
    s.pending[0](streamResponse('Hello 👋 नमस्ते'));
    await request;
    assert.equal(s.messages[1].innerText, 'Hello 👋 नमस्ते');
    assert.match(s.messages[1].title, /First text: 10 ms/);
});

test('midstream error preserves partial answer and marks it incomplete', async () => {
    const s = setup(); s.input.value = 'hi';
    const request = s.context.sendCommand();
    await new Promise(setImmediate);
    s.pending[0](streamResponse('Partial answer', {type: 'error', message: 'Try again.'}));
    await request;
    assert.match(s.messages[1].innerText, /Partial answer.*Incomplete response.*Try again/s);
});

test('EOF without done is treated as incomplete', async () => {
    const s = setup(); s.input.value = 'hi';
    const request = s.context.sendCommand();
    await new Promise(setImmediate);
    s.pending[0](streamResponse('Partial', {type: 'start'}));
    await request;
    assert.match(s.messages[1].innerText, /Incomplete response/);
});

test('voice reports permission errors and permits retry', () => {
    const s = setup(); let recognizer;
    s.context.window.SpeechRecognition = class {constructor(){recognizer=this} start(){} stop(){} abort(){}};
    s.context.startListening(); recognizer.onerror({error:'not-allowed'});
    assert.match(s.messages[0].innerText, /blocked/);
    s.context.startListening(); recognizer.onerror({error:'network'});
    assert.match(s.messages[1].innerText, /speech recognition service/);
});
test('voice sends a final transcript once, not interim speech', () => {
    const s=setup();let recognizer;let sent=0;
    s.context.window.SpeechRecognition=class {constructor(){recognizer=this} start(){} stop(){} abort(){this.onend()}};
    s.context.sendCommand=()=>{sent++};
    s.context.startListening();
    const result=[{transcript:'hello'}];result.isFinal=false;
    recognizer.onresult({results:[result]});assert.equal(sent,0);
    result.isFinal=true;recognizer.onresult({results:[result]});
    recognizer.onresult({results:[result]});assert.equal(sent,1);assert.equal(s.input.value,'hello');
});
