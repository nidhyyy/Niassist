# Niassist

**Ask. Create. Make it happen.**

Niassist is a personal AI assistant that connects conversations, document answers,
and tasks. Ask follow-up questions, find evidence in your files, review suggested
checklists, and track reminders in one workspace.

## What you can do

- Chat with streamed responses and saved conversation history.
- Speak a message using a browser that supports speech recognition.
- Upload PDF or TXT files and ask questions with supporting source quotes.
- Generate task suggestions from documents or conversations, then edit and confirm them.
- Create tasks, set deadlines and reminders, and track completion.
- Review in-app notifications and activity history.
- Switch between light and dark modes in the retro-inspired chat and workspace.

## Stack

Python · Flask · Google Gemini · SQLite · HTML/CSS/JavaScript

The web application serves the interface and APIs. A separate Python worker
processes uploaded documents and delivers due reminders. Node.js is only needed
for frontend tests; there is no frontend build step to run the app.

## Before you start

- Python **3.12 or 3.13** (3.12 is the deployment target).
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/).
- Internet access for AI requests. API usage is subject to your provider's limits.

Download and extract the source, or clone the repository once this revision has
been uploaded. Open a terminal in the folder containing `app.py`.

### Windows — Command Prompt

```bat
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install -r requirements.txt
if not exist .env copy .env.example .env
notepad .env
```

Set your key in `.env`:

```dotenv
GEMINI_API_KEY=your_actual_api_key
GEMINI_MODEL=gemini-2.5-flash
```

Start the web application:

```bat
.\.venv313\Scripts\python.exe app.py
```

Open **another terminal in the same project folder** and start the worker:

```bat
.\.venv313\Scripts\python.exe worker.py
```

### Linux / macOS

With Python 3.12 or 3.13 installed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

Edit `.env` and set the key shown above. Then run:

```bash
.venv/bin/python app.py
```

In a second terminal in the project folder:

```bash
.venv/bin/python worker.py
```

### Open Niassist

Visit **http://localhost:5000**. Create an account using an email and a password
of 12–128 characters. Keep both terminals running while using the app. Press
`Ctrl+C` in each terminal to stop it.

## Your first workflow

1. Start a chat, then ask a related follow-up question.
2. Open **Documents & tasks** and upload your own small PDF or TXT file.
3. Wait until processing finishes, select the document, and ask a question.
4. Check the source quotes attached to the answer.
5. Request a checklist, review the suggestions, and confirm the tasks to save.
6. Choose a future reminder time and check the **Inbox** when it is due.

AI suggestions do not create or schedule tasks until you confirm them.

## Voice input

Click the microphone, allow browser microphone access, and speak when
**Listening…** appears. A final transcript is sent automatically. Click the
microphone again to finish listening.

Browser support varies. Try Chrome for local testing. Use `localhost` locally
or HTTPS for a public deployment. Speech recognition may use the browser's
online service; a speech-service network error is separate from a Gemini error.
The interface reports permission, microphone, and network failures individually.

## Troubleshooting

| Problem | Check |
| --- | --- |
| AI request fails | Check your Gemini key, model access, quota, and internet connection. |
| Documents remain queued | Start `worker.py` from the same project folder and environment. |
| A reminder has not arrived | Keep the worker running and check the chosen date and time zone. Reminders appear in the in-app Inbox. |
| Voice reports a network error | Check browser speech support and try another network. |
| Updated design does not appear | Restart Flask and refresh with `Ctrl+F5`. |
| Dependency installation tries to compile native packages | Recreate the virtual environment using Python 3.12 or 3.13. |

## Data and limits

- Accounts, conversations, documents, and tasks are stored in SQLite. Local data
  and the session secret live in `instance/` by default.
- Preserve `.env` and `instance/` when updating. Never upload them to GitHub.
- Upload limits: 5 MB per file, 100 PDF pages, and 20 documents per account.
- Scanned PDFs require OCR elsewhere before use. Built-in OCR is not included.
- Document retrieval uses keyword ranking, not embedding-based semantic search.
- Chat context includes up to 10 recent completed turns, bounded to 24,000 characters.
- Reminders are in-app only: no email, SMS, or push notifications.
- Verify important AI answers against the original source. A matching quote does
  not guarantee the model's interpretation is correct.

## Development and tests

Install development dependencies using your virtual environment:

```bat
.\.venv313\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv313\Scripts\python.exe -m pytest -q
```

On Linux/macOS, use `.venv/bin/python` instead. For frontend tests, install
Node.js 22, then run:

```bash
npm ci
npm test
```

Tests mock AI requests and browser speech events. Frontend tests use simulated
DOMs; they do not prove live microphone access or visual layout in a real browser.

## Project structure

| Path | Purpose |
| --- | --- |
| `app.py`, `accounts.py` | Flask app, authentication, and sessions |
| `conversations.py` | Saved chats and context |
| `ai/` | Gemini chat and structured-output adapters |
| `workflow_routes.py`, `workflow_store.py` | Documents, tasks, and reminder APIs/data |
| `worker.py`, `document_parser.py` | Background processing |
| `templates/`, `static/` | Chat and workspace interfaces |
| `tests/` | Automated backend and frontend tests |
| `deploy/`, `scripts/` | Deployment configuration and backup tooling |
| `legacy/` | Separate older desktop experiment; not required by the web app |

## Deployment

Local Flask commands above are for development. For Ubuntu 24.04, Nginx,
Gunicorn, systemd, HTTPS, backups, and GitHub Actions setup, follow
[RELEASE-GUIDE.md](RELEASE-GUIDE.md). Review resource requirements before using
the defaults on a small EC2 instance. The latest application revision still needs
live deployment verification.

Keep `.env.example` and deployment templates: they contain setup placeholders,
not your real secrets. Keep `tests/` for CI. Never commit virtual environments,
`instance/`, logs, credentials, or database backups.
