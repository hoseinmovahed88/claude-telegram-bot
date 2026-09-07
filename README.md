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

Run **one bot per project**: define several in `bots.toml` and they come up
together in one process, each with its own token, workspace and session, and
nothing shared between them.

---

## ⚠️ Read this before you run it

**This bot gives whoever can message it the ability to read, write, and execute
code on the machine it runs on.** Treat the bot token like an SSH key.

Four things stand between a Telegram message and your filesystem, and all
four are on by default:

1. **An allow-list.** Only the numeric user ids you name may use the bot; it
   refuses to start without one. Everyone else is ignored in silence — a reply
   would confirm to a stranger that the bot is live — while every attempt is
   logged with the sender's id and username, which is how you get someone's id
   if you do want to add them. Set `reply_to_strangers` to answer instead.
2. **Private chats only.** An allow-listed user is still refused in a group,
   because the bot's replies carry file contents and command output to
   everyone who can read the chat. Name a chat in `allowed_chat_ids` to opt it
   in deliberately; doing so replaces the private-only default rather than
   adding to it, and still admits only allow-listed users.
3. **A workspace.** Sessions run inside the bot's `workspace` and `/cd` cannot
   escape it. Point it at a project directory, not at `$HOME`.
4. **Per-tool approval.** In the default permission mode every tool call waits
   for you to tap ✅ in Telegram, and auto-denies after `permission_timeout`.

`/mode bypassPermissions` removes the fourth one. Don't leave it on.

Two more things worth knowing. Editing an old message does **not** re-run it
as a prompt, and the bot subscribes only to `message` and `callback_query`
updates, so channel posts, inline queries and chat-member events never reach
it at all. And the bot token is itself a credential: anyone holding it can
read the updates addressed to your bot. Keep it out of the repo — `bots.toml`
and `.env` are both git-ignored, and `token_env` keeps the token out of the
config file entirely.

Running the whole thing inside a container or a VM is the safe way to use it.

---

## Setup

```bash
git clone https://github.com/hoseinmovahed88/claude-telegram-bot
cd claude-telegram-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp bots.example.toml bots.toml     # several bots
# ...or, for a single bot:
cp .env.example .env
```

You also need the Claude Code CLI on `PATH` — the SDK drives it:

```bash
npm install -g @anthropic-ai/claude-code
claude login          # or set ANTHROPIC_API_KEY in .env
```

Then configure. You need, per bot: a token from
[@BotFather](https://t.me/BotFather), your numeric id from
[@userinfobot](https://t.me/userinfobot), and a workspace directory.

Run it:

```bash
python -m claude_telegram_bot                 # bots.toml, or .env if absent
python -m claude_telegram_bot /etc/bots.toml  # an explicit config file
```

---

## Several bots, one per project

Create a separate bot in @BotFather for each project, then list them:

```toml
# bots.toml
[defaults]
allowed_user_ids = [11111111]
permission_mode = "default"

[bots.work]
token_env = "WORK_BOT_TOKEN"
workspace = "~/code/work"

[bots.blog]
token_env = "BLOG_BOT_TOKEN"
workspace = "~/writing/blog"
permission_mode = "acceptEdits"
model = "claude-sonnet-5"
```

```
2026-09-07 14:02:11 INFO | starting 2 bot(s): work (8412:***), blog (7735:***)
2026-09-07 14:02:12 INFO | bot 'work' polling as @my_work_bot | workspace=/home/me/code/work ...
2026-09-07 14:02:12 INFO | bot 'blog' polling as @my_blog_bot | workspace=/home/me/writing/blog ...
2026-09-07 14:02:12 INFO | 2 bot(s) running; press Ctrl-C to stop
```

`[defaults]` sets anything the per-bot tables may override. Tokens can be
inline (`token`) or, better, read from the environment (`token_env`), which a
`.env` file next to `bots.toml` can supply.

**What "isolated" means here.** Each bot builds its own session manager, its
own permission registry and its own Claude sessions, so between two bots
nothing is shared: not conversations, not "always allow" grants, not pending
permission buttons — pressing one bot's button can never answer another's.
Each Claude session is also given its own generated session id, because the
CLI otherwise inherits `CLAUDE_CODE_SESSION_ID` from its parent environment
and every session started from one process would land on the same id.

Two bots may not share a token: Telegram allows a single poller per token, so
they would steal each other's updates. The loader rejects that at startup
rather than letting you debug it later.

If `bots.toml` is absent, the bot falls back to single-bot mode from `.env`
and behaves exactly as before.

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

`bots.toml` is the primary form (see `bots.example.toml`); `.env` is the
single-bot fallback (see `.env.example`).

### bots.toml

| Key | Where | Purpose |
| --- | --- | --- |
| `token` / `token_env` | per bot | **Required.** The token, or the env var holding it |
| `workspace` | per bot | **Required.** Directory that bot is confined to |
| `allowed_user_ids` | either | **Required.** Numeric Telegram user ids |
| `allowed_chat_ids` | either | Chats the bot may be used in (default: private chats only) |
| `reply_to_strangers` | either | Answer non-allow-listed users instead of ignoring them |
| `permission_mode` | either | `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`, `auto` |
| `model`, `effort` | either | Model id and reasoning effort |
| `allowed_tools` | either | Tools approved without asking |
| `disallowed_tools` | either | Tools the agent may never use |
| `max_turns` | either | Cap on agentic turns per prompt |
| `permission_timeout` | either | Seconds before an unanswered prompt auto-denies |
| `verbose` | either | Start with thinking and tool results shown |

"Either" means it can go in `[defaults]` and be overridden per bot. Unknown
keys are an error, not a silent no-op, so a typo can't quietly disable a
setting.

### .env (single-bot fallback)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | **Required.** BotFather token |
| `TELEGRAM_ALLOWED_USER_IDS` | — | **Required.** Comma-separated user ids |
| `TELEGRAM_ALLOWED_CHAT_IDS` | *(private only)* | Chats the bot may be used in |
| `TELEGRAM_REPLY_TO_STRANGERS` | `0` | Answer non-allow-listed users |
| `ANTHROPIC_API_KEY` | — | Passed to the CLI; or run `claude login` |
| `BOT_NAME` | `default` | Name shown in `/status` and the logs |
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
                     runner.py  ── one process, N bots ──┐
                         │                               │
  bot "work" ── Application ── SessionManager ── ChatSession ── ClaudeSDKClient ─▸ claude CLI
                 (own token)   (own registry)   (own cwd)                          ~/code/work

  bot "blog" ── Application ── SessionManager ── ChatSession ── ClaudeSDKClient ─▸ claude CLI
                 (own token)   (own registry)   (own cwd)                          ~/writing/blog
```

Within one bot:

```
Telegram  ──update──▸  handlers.py  ──prompt──▸  ChatSession
                                                     │
                                          ClaudeSDKClient (Agent SDK)
                                                     │
                                             claude CLI  ──▸  your files
                                                     │
Telegram  ◂──messages──  TelegramSink  ◂──stream──  ChatSession._pump
```

- **`runner.py`** starts every bot, waits for SIGINT/SIGTERM, then unwinds them
  in reverse. It closes each bot's Claude sessions explicitly, because PTB runs
  the `post_shutdown` hook only from `run_polling()` — which the multi-bot path
  does not use, so the CLI subprocesses would otherwise be orphaned.
- **`config.py`** loads `bots.toml` or falls back to `.env`, and refuses to
  start on an empty allow-list, a missing workspace, a shared token or an
  unknown key.
- **`session.py`** holds one `ClaudeSDKClient` per chat and pumps
  `receive_response()` into the chat: text as messages, tool calls as labelled
  log lines, the `ResultMessage` as a duration/turns/cost footer.
- **`permissions.py`** implements the SDK's `can_use_tool` callback by sending
  an inline keyboard and awaiting the tap. The SDK dispatches permission
  requests in their own task, so waiting for a human blocks only the tool call
  being asked about — `/stop` still gets through, and an interrupt denies
  whatever is still pending. Each bot owns its `PermissionRegistry`, so a
  button press is routed only within the bot that asked.
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
bot token and no API key needed. `tests/test_access.py` pins the security
boundary against real `telegram.Update` objects, `tests/test_isolation.py` the
multi-bot guarantees, and `tests/test_runner.py` the start/stop lifecycle.

---

## License

MIT
