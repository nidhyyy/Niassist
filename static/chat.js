function addMessage(text, className) {
    const chatBox = document.getElementById('chatBox');
    const div = document.createElement('div');
    div.classList.add('message', className);
    div.innerText = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    return div;
}

let csrfToken = '';
let signedIn = false;
let activeChat = null;
let queue = Promise.resolve();
let pendingCount = 0;
let chatReady = false;

function chatControls(busy) {
    ['chat-select', 'new-chat', 'delete-chat', 'logout-button', 'import-chats', 'tasks-from-chat'].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.disabled = busy;
    });
}

async function api(url, options = {}) {
    const response = await fetch(url, {...options, headers: {'X-CSRF-Token': csrfToken, ...options.headers}});
    const data = await response.json();
    if (!response.ok) {
        if (response.status === 401 && signedIn) showLogin();
        throw new Error(data.error?.message || 'Unable to load chats.');
    }
    return data;
}

async function refreshChatList() {
    const selector = document.getElementById('chat-select');
    if (!selector) return;
    const data = await api('/api/chats');
    selector.replaceChildren();
    for (const chat of data.chats) {
        const option = document.createElement('option');
        option.value = chat.id;
        option.textContent = chat.title;
        selector.appendChild(option);
    }
    selector.value = activeChat;
}

async function loadChat(id) {
    const data = await api('/api/chats/' + id);
    activeChat = id;
    document.getElementById('chatBox').replaceChildren();
    for (const turn of data.turns) {
        addMessage(turn.question, 'user');
        addMessage(turn.answer, 'bot');
    }
    if (!data.turns.length) addMessage('New chat. Ask your first question.', 'bot');
}

async function initializeChats() {
    chatControls(true);
    try {
        const data = await api('/api/chats');
        activeChat = data.chats.length ? data.chats[0].id : (await api('/api/chats', {method: 'POST'})).id;
        await loadChat(activeChat);
        await refreshChatList();
        chatReady = true;
    } catch (error) {
        addMessage(error.message + ' Refresh the page to retry.', 'bot');
    } finally {
        chatControls(false);
    }
}

async function changeChat(action) {
    if (pendingCount) return;
    chatReady = false;
    chatControls(true);
    try {
        if (action === 'delete') {
            if (!window.confirm('Delete this chat and its saved messages?')) return;
            await api('/api/chats/' + activeChat, {method: 'DELETE'});
            await initializeChats();
        } else if (action === 'new') {
            const data = await api('/api/chats', {method: 'POST'});
            await loadChat(data.id);
            await refreshChatList();
        } else {
            await loadChat(document.getElementById('chat-select').value);
        }
    } catch (error) {
        addMessage(error.message, 'bot');
        // Restore the selector if loading another chat failed.
        document.getElementById('chat-select').value = activeChat;
    } finally {
        chatReady = Boolean(activeChat);
        chatControls(false);
    }
}

function sendCommand() {
    const input = document.getElementById('command');
    const text = input.value.trim();
    if (!text) return;
    if (text.length > 4000) {
        addMessage('Please keep your message within 4000 characters.', 'bot');
        return;
    }
    const hasChats = Boolean(document.getElementById('chat-select'));
    if (hasChats && !chatReady) {
        addMessage('Chats are still loading. Please wait or refresh the page.', 'bot');
        return Promise.resolve();
    }
    addMessage(text, 'user');
    input.value = '';
    const reply = addMessage(pendingCount ? 'Queued — waiting for the previous answer…' : 'Niassist is thinking...', 'bot');
    const cid = activeChat;
    pendingCount++;
    chatControls(true);
    const task = queue.then(() => requestAnswer(text, reply, cid));
    queue = task.catch(() => {});
    return task.finally(() => {
        pendingCount--;
        if (!pendingCount) chatControls(false);
    });
}

async function requestAnswer(text, reply, cid) {
    if (cid && !signedIn) { reply.innerText = 'Please sign in and resend your question.'; return; }
    reply.innerText = 'Niassist is thinking...';
    const controller = new AbortController();
    let answer = '';
    let reader;
    let timer;
    const resetTimeout = () => {
        clearTimeout(timer);
        timer = setTimeout(() => controller.abort(), 30000);
    };
    const showFailure = (message) => {
        reply.innerText = answer ? answer + '\n\n[Incomplete response] ' + message : message;
    };
    resetTimeout();
    try {
        const res = await fetch('/command/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
            body: JSON.stringify({ text, ...(cid ? {conversation_id: cid} : {}) }),
            signal: controller.signal,
        });
        if (!res.ok) {
            if (res.status === 401 && signedIn) showLogin();
            const data = await res.json();
            showFailure(data.error?.message || 'Unable to complete your request. Please try again.');
            return;
        }
        if (!res.body) throw new Error('Streaming unavailable');
        reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let completed = false;
        let failed = false;
        const consumeLine = (line) => {
            if (!line.trim() || completed || failed) return;
            const event = JSON.parse(line);
            if (event.type === 'delta' && typeof event.text === 'string') {
                answer += event.text;
                reply.innerText = answer;
                const chat = document.getElementById('chatBox');
                chat.scrollTop = chat.scrollHeight;
            } else if (event.type === 'error') {
                failed = true;
                showFailure(event.message || 'The response was interrupted.');
            } else if (event.type === 'done') {
                completed = true;
                reply.title = `First text: ${event.first_text_ms} ms; total: ${event.total_ms} ms`;
            }
        };
        while (!completed && !failed) {
            const { value, done } = await reader.read();
            resetTimeout();
            buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            lines.forEach(consumeLine);
            if (done) {
                consumeLine(buffer);
                break;
            }
        }
        if (!completed && !failed) showFailure('Connection ended early. Please try again.');
        if (completed && cid) {
            try { await refreshChatList(); } catch (_) { /* Saved answer remains visible. */ }
        }
    } catch (err) {
        showFailure(err.name === 'AbortError'
            ? 'No response data arrived for 30 seconds. Please try again.'
            : 'Connection interrupted. Check your connection and try again.');
    } finally {
        clearTimeout(timer);
        if (reader) {
            try { await reader.cancel(); } catch (_) { /* Connection already closed. */ }
            reader.releaseLock();
        }
        controller.abort();
    }
}

function fillSuggestion(element) {
    document.getElementById('command').value = element.innerText;
}

let listening = false;
let voiceSession = null;
function startListening() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        addMessage('Voice input is unavailable in this browser. Try Chrome, or type your message.', 'bot');
        return;
    }
    if (voiceSession) {
        voiceSession.stop();
        return;
    }
    if (window.isSecureContext === false) {
        addMessage('Microphone access needs a secure page. For local testing, open http://localhost:5000 on the computer running Flask. For a public site, use HTTPS.', 'bot');
        return;
    }
    const button = document.querySelector?.('.mic-btn');
    const original = button?.innerHTML;
    const status = addMessage('Starting microphone… Allow microphone access if your browser asks.', 'bot');
    let recognition;
    let finished = false;
    let timer;
    function cleanup() {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        listening = false;
        voiceSession = null;
        if (button) {
            button.innerHTML = original;
            button.setAttribute('aria-label', 'Speak and send a message');
            button.setAttribute('aria-pressed', 'false');
        }
    }
    function fail(code) {
        const messages = {
            'not-allowed': 'Microphone access was blocked. Allow Microphone in this site’s browser permissions, check Windows microphone privacy settings, then retry.',
            'service-not-allowed': 'Your browser blocked its speech recognition service. Try Chrome with speech recognition available, or type your message.',
            'audio-capture': 'No microphone audio is available. Check that your microphone is connected, enabled, and selected in browser settings.',
            'no-speech': 'No speech was detected. Click the microphone again and speak after “Listening” appears.',
            'network': 'The browser could not reach its speech recognition service. Check your internet connection and retry. This is separate from the Gemini service.',
            'language-not-supported': 'The browser does not support English (India) speech recognition. Try another supported browser.',
            'aborted': 'Voice input stopped. Click the microphone to try again.'
        };
        status.innerText = messages[code] || 'Voice input could not start (' + code + '). Please retry or type your message.';
        cleanup();
    }
    try {
        recognition = new SpeechRecognition();
        recognition.lang = 'en-IN';
        recognition.continuous = false;
        recognition.interimResults = true;
        recognition.maxAlternatives = 1;
        recognition.onstart = () => {
            if (finished) return;
            status.innerText = 'Listening… Speak now. Click the microphone again to finish.';
            if (button) button.innerHTML = '■';
        };
        recognition.onresult = (event) => {
            if (finished) return;
            let transcript = '';
            let final = false;
            for (let i = 0; i < event.results.length; i++) {
                transcript += event.results[i][0].transcript + ' ';
                final = final || event.results[i].isFinal;
            }
            transcript = transcript.trim();
            status.innerText = transcript ? 'Heard: ' + transcript : 'Listening…';
            if (final && transcript) {
                document.getElementById('command').value = transcript;
                status.innerText = 'Voice captured. Sending your message…';
                cleanup();
                recognition.abort();
                sendCommand();
            }
        };
        recognition.onerror = event => { if (!finished) fail(event.error); };
        recognition.onend = () => {
            if (!finished) {
                status.innerText = 'Voice input ended without a final transcript. Try again and speak after “Listening” appears.';
                cleanup();
            }
        };
        listening = true;
        voiceSession = recognition;
        if (button) {
            button.setAttribute('aria-label', 'Stop voice input');
            button.setAttribute('aria-pressed', 'true');
        }
        timer = setTimeout(() => {
            if (!finished) {
                status.innerText = 'Voice input timed out. Check microphone permission and your connection, then retry.';
                cleanup();
                recognition.abort();
            }
        }, 45000);
        recognition.start();
    } catch (error) {
        fail(error.name === 'NotAllowedError' ? 'not-allowed' : error.name || 'start-failed');
    }
}

document.getElementById('command').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.isComposing) {
        event.preventDefault();
        sendCommand();
    }
});


if (document.getElementById('chat-select')) {
    document.getElementById('chat-select').addEventListener('change', () => changeChat('select'));
    document.getElementById('new-chat').addEventListener('click', () => changeChat('new'));
    document.getElementById('delete-chat').addEventListener('click', () => changeChat('delete'));
    if (document.getElementById('auth-panel')) initializeAccount();
    else initializeChats();
}


function showLogin() {
    signedIn = false;
    chatReady = false;
    activeChat = null;
    document.getElementById('chatBox').replaceChildren();
    document.getElementById('chat-select').replaceChildren();
    document.getElementById('account-email').textContent = '';
    document.getElementById('import-chats').hidden = true;
    document.getElementById('auth-panel').hidden = false;
}

async function initializeAccount() {
    try {
        const data = await api('/api/auth/session');
        csrfToken = data.csrf_token;
        signedIn = Boolean(data.user);
        document.getElementById('auth-panel').hidden = signedIn;
        document.getElementById('import-chats').hidden = !data.guest_chats;
        if (signedIn) {
            document.getElementById('account-email').textContent = data.user.email;
            await initializeChats();
        }
    } catch (error) {
        document.getElementById('auth-message').textContent = error.message;
    }
}

async function submitAccount(action) {
    const form = document.getElementById('auth-form');
    if (!form.reportValidity()) return;
    const buttons = ['login-button', 'register-button'];
    buttons.forEach(id => { document.getElementById(id).disabled = true; });
    const message = document.getElementById('auth-message');
    message.textContent = 'Please wait…';
    try {
        // Refresh CSRF after a logout/session expiry, without replaying a password request.
        const session = await api('/api/auth/session');
        csrfToken = session.csrf_token;
        const data = await api('/api/auth/' + action, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email: document.getElementById('auth-email').value,
                                  password: document.getElementById('auth-password').value}),
        });
        csrfToken = data.csrf_token;
        document.getElementById('auth-password').value = '';
        message.textContent = '';
        await initializeAccount();
    } catch (error) {
        message.textContent = error.message;
    } finally {
        buttons.forEach(id => { document.getElementById(id).disabled = false; });
    }
}

if (document.getElementById('auth-panel')) {
    document.getElementById('auth-form').addEventListener('submit', event => {
        event.preventDefault(); submitAccount('login');
    });
    document.getElementById('register-button').addEventListener('click', () => submitAccount('register'));
    document.getElementById('logout-button').addEventListener('click', async () => {
        if (pendingCount) return;
        try {
            await api('/api/auth/logout', {method: 'POST'});
            showLogin();
            await initializeAccount();
        } catch (error) { addMessage(error.message, 'bot'); }
    });
    document.getElementById('import-chats').addEventListener('click', async () => {
        if (pendingCount || !window.confirm('Move this browser’s previous chats into the signed-in account?')) return;
        chatControls(true);
        try {
            await api('/api/auth/import', {method: 'POST'});
            await initializeAccount();
        } catch (error) { addMessage(error.message, 'bot'); }
        finally { chatControls(false); }
    });
}

if (document.getElementById('tasks-from-chat')) {
    document.getElementById('tasks-from-chat').addEventListener('click', () => {
        if (activeChat && !pendingCount && signedIn) location.href = '/workspace?chat=' + encodeURIComponent(activeChat);
    });
}
