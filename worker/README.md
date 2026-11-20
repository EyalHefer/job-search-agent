# Telegram inbox (Cloudflare Worker)

My whole routine with the agent happens in one private Telegram chat, on my phone:

- **Job posts** from the two WhatsApp groups with Advanced Chat Privacy, which cannot be exported.
  I respect that setting: nothing here reads WhatsApp. I select posts by hand and share them to the bot.
- **Chat exports** of the other groups: WhatsApp → Export chat → without media → Telegram, instead of
  email. A newer export of a group replaces the older one.
- **`/run`** starts the agent on GitHub Actions. The digest comes back to the same chat.
- **Feedback**: every digest job has "✅ הגשתי / ❌ לא הגשתי" buttons, and a reply to a job's message
  adds my own match and reason, for example `60 דורש 3 שנות ניסיון`.

```
phone ──share / buttons / /run──> Telegram bot ──webhook──> Worker ──> KV
                                                              │
                                     /run: workflow_dispatch  └──> GitHub Actions: agent run
                                                                     ├─ GET /inbox, /exports
                                                                     └─ digest ──> Telegram chat
```

| Route | Who calls it | What it does |
|---|---|---|
| `POST /telegram` | Telegram | Checks Telegram's secret header and my chat id, then stores a post, an export file or feedback, or runs a command |
| `GET /inbox` | the agent | Waiting posts and feedback events, oldest first: `{"messages": [...], "events": [...]}` |
| `POST /inbox/ack` | the agent | Deletes the posts and events the agent has saved on disk: `{"update_ids": [...]}` |
| `GET /exports` | the agent | The saved chat exports: `{"exports": [{"name", "received_at", "size"}]}` |
| `GET /exports/file?name=` | the agent | One export's bytes |

The agent saves first and acks second, so a crash in between can repeat a message but never lose one.
Exports stay until replaced, like files kept on disk. Anything the agent never collects expires after 30 days.

The Worker knows nothing about jobs. A button press is saved with the id of the message it was on;
the agent remembers which job it sent in each message.

## Security

- `/telegram` accepts only requests carrying `TELEGRAM_SECRET` in `X-Telegram-Bot-Api-Secret-Token`.
- Messages from any chat other than `CHAT_ID` are dropped without being stored.
- `/inbox` routes need `Authorization: Bearer <INBOX_TOKEN>`. Secrets are compared in constant time.
- The Worker holds the bot token only to download shared export files and mark the pressed button.
  Replies go out by answering the webhook with a method call.
- `/run` uses a fine-grained GitHub token limited to "Actions: write" on the private repo. The worst a
  leak allows is starting extra runs.
- Export file names are cut to their last part, so a name cannot point outside the agent's folder.

## Develop

```
npm install
npm test
```

## Deploy

```
npx wrangler login
npx wrangler kv namespace create INBOX      # paste the id into wrangler.toml
npx wrangler secret put TELEGRAM_SECRET
npx wrangler secret put INBOX_TOKEN
npx wrangler secret put CHAT_ID
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put GITHUB_TOKEN        # optional, for /run; set GITHUB_REPO in wrangler.toml
npx wrangler deploy
```

Then connect the bot. Put `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SECRET` and `INBOX_URL` in the private `.env`,
send the bot any message, and run:

```
python connect_bot.py <path to .env>
```

It finds your chat id from that message, saves it to `.env` as `CHAT_ID`, and sets the bot's webhook to
`<INBOX_URL>/telegram` with the secret. It never prints the token, the secret or the chat id. Upload the
chat id afterwards, from the folder holding `.env` (no non-ASCII paths in the command, since a right-to-left
path can come out reversed when pasted):

```
grep "^CHAT_ID=" .env | cut -d= -f2- | tr -d '

' | (cd <this folder> && npx wrangler secret put CHAT_ID)
```
