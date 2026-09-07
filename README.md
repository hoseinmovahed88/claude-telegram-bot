# claude-telegram-bot

Watch and drive a live **Claude Code** session from Telegram.

Send the bot a message and it becomes a prompt for a real Claude Code session
running on your machine. Every step the agent takes — each tool call, each
result, each cost figure — arrives as its own Telegram message, so the chat
reads like the session transcript rather than a chat completion.

```
you   ▸ why is the login test failing?

      🔍 Grep
      login_test  in tests/

      📖 Read
      tests/test_login.py

      🔐 Permission requested
      💻 Bash
      pytest tests/test_login.py -x
      [ ✅ Allow ]  [ ⛔️ Deny ]
      [ ♾ Always allow Bash ]
      [ 🛑 Deny & stop ]

      The fixture seeds an expired token. Line 42 sets `exp` to
      `now - 3600`; the middleware rejects it before the handler runs.

      ✔️ 12.4s · 4 turns · $0.0231
```

Built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk),
so the session has the full Claude Code tool set — Read, Write, Edit, Bash,
Glob, Grep, WebFetch, subagents, skills, MCP servers and your `CLAUDE.md`.

---

## ⚠️ Read this before you run it

**This bot gives whoever can message it the ability to read, write, and execute
code on the machine it runs on.** Treat the bot token like an SSH key.

Three things stand between a Telegram message and your filesystem, and all
three are on by default:

1. **An allow-list.** `TELEGRAM_ALLOWED_USER_IDS` is mandatory — the bot
   refuses to start without it. Everyone else gets a rejection.
2. **A workspace.** Sessions run inside `CLAUDE_WORKSPACE` and `/cd` cannot
   escape it. Point it at a scratch directory, not at `$HOME`.
3. **Per-tool approval.** In the default permission mode every tool call waits
   for you to tap ✅ in Telegram, and auto-denies after `PERMISSION_TIMEOUT`.

`/mode bypassPermissions` removes the third one. Don't leave it on.

Running the whole thing inside a container or a VM is the safe way to use it.

---

## Setup

```bash
git clone https://github.com/hoseinmovahed88/claude-telegram-bot
cd claude-telegram-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
$EDITOR .env
```

You also need the Claude Code CLI on `PATH` — the SDK drives it:

```bash
npm install -g @anthropic-ai/claude-code
claude login          # or set ANTHROPIC_API_KEY in .env
```

Then fill in `.env`:

- **`TELEGRAM_BOT_TOKEN`** — from [@BotFather](https://t.me/BotFather).
- **`TELEGRAM_ALLOWED_USER_IDS`** — your numeric id, from
  [@userinfobot](https://t.me/userinfobot). Comma-separated for several people.
- **`CLAUDE_WORKSPACE`** — the directory the agent is confined to.

Run it:

```bash
python -m claude_telegram_bot
```

---

## Commands

| Command | What it does |
| --- | --- |
| *(any message)* | Send it to the session as a prompt |
| `/new` | End this session, start a fresh one |
| `/resume <id>` | Continue an earlier session |
| `/sessions` | List recent sessions in the current directory |
| `/stop` | Interrupt the turn that is running |
| `/status` | Session id, directory, model, mode, turns, spend |
| `/context` | Context-window usage |
| `/cd <path>` | Change working directory (within the workspace) |
| `/mode <mode>` | `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`, `auto` |
| `/model <name>` | Set the model; `-` restores the default |
| `/verbose` | Toggle thinking blocks and tool results |
| `/allow` | Show, or `/allow clear` revoke, "always allow" grants |

`/mode` and `/model` apply to the running session immediately. `/cd`, `/new`
and `/resume` start a new one on your next message.

---

## Configuration

Everything lives in `.env` (see `.env.example` for the annotated version).

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | **Required.** BotFather token |
| `TELEGRAM_ALLOWED_USER_IDS` | — | **Required.** Comma-separated user ids |
| `ANTHROPIC_API_KEY` | — | Passed to the CLI; or run `claude login` |
| `CLAUDE_WORKSPACE` | `./workspace` | Directory the agent is confined to |
| `CLAUDE_MODEL` | CLI default | Model alias or full id |
| `CLAUDE_PERMISSION_MODE` | `default` | Starting permission mode |
| `CLAUDE_EFFORT` | CLI default | `low` … `max` |
| `CLAUDE_ALLOWED_TOOLS` | *(none)* | Tools approved without asking, e.g. `Read,Glob,Grep` |
| `CLAUDE_DISALLOWED_TOOLS` | *(none)* | Tools the agent may never use |
| `CLAUDE_MAX_TURNS` | unlimited | Cap on agentic turns per prompt |
| `PERMISSION_TIMEOUT` | `300` | Seconds before an unanswered prompt auto-denies |
| `VERBOSE_DEFAULT` | `0` | Start with thinking and tool results shown |

Note that `CLAUDE_ALLOWED_TOOLS` pre-approves a tool *before* the Telegram
prompt is reached — that is what it is for, but it means those tools run
without asking you.

---

## How it works

```
Telegram  ──update──▸  handlers.py  ──prompt──▸  ChatSession
                                                     │
                                          ClaudeSDKClient (Agent SDK)
                                                     │
                                             claude CLI  ──▸  your files
                                                     │
Telegram  ◂──messages──  TelegramSink  ◂──stream──  ChatSession._pump
```

- **`session.py`** holds one `ClaudeSDKClient` per chat and pumps
  `receive_response()` into the chat: text as messages, tool calls as labelled
  log lines, the `ResultMessage` as a duration/turns/cost footer.
- **`permissions.py`** implements the SDK's `can_use_tool` callback by sending
  an inline keyboard and awaiting the tap. The SDK dispatches permission
  requests in their own task, so waiting for a human blocks only the tool call
  being asked about — `/stop` still gets through, and an interrupt denies
  whatever is still pending.
- **`render.py`** turns SDK message types into Telegram HTML: per-tool
  summaries, escaping, and splitting at line boundaries to stay under
  Telegram's 4096-character limit.
- **`sink.py`** does the sending, keeps the typing indicator alive, and falls
  back to plain text if Telegram rejects a fragment.

The bot runs with `concurrent_updates(True)`, which is what lets a permission
button be handled while a turn is still running.

One turn runs at a time per chat; a prompt sent mid-turn is refused with a
note rather than queued.

---

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests replace Telegram with a recording fake and the SDK client with a
canned transcript, so the whole suite runs offline in about two seconds — no
bot token and no API key needed.

---

## License

MIT
