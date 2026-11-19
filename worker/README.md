# Telegram inbox (Cloudflare Worker)

Two of my job groups on WhatsApp have Advanced Chat Privacy turned on, so their chats cannot be
exported. I respect that setting: nothing here reads WhatsApp. Instead, when I see a job post there,
I share it by hand to a private Telegram bot. This Worker keeps those messages until the agent,
which runs on my laptop only when I start it, collects them. The laptop does not need to be on.

```
phone ──share──> Telegram bot ──webhook──> Worker ──> KV ──GET /inbox──> agent on laptop
```

| Route | Who calls it | What it does |
|---|---|---|
| `POST /telegram` | Telegram | Checks Telegram's secret header and my chat id, stores the text in KV, replies "נקלט ✓" |
| `GET /inbox` | the agent | Returns all waiting messages, oldest first |
| `POST /inbox/ack` | the agent | Deletes the messages the agent has saved on disk: `{"update_ids": [...]}` |

The agent saves first and acks second, so a crash in between can repeat a message but never lose one.
Messages the agent never collects expire after 30 days.

## Security

- `/telegram` accepts only requests carrying `TELEGRAM_SECRET` in `X-Telegram-Bot-Api-Secret-Token`.
- Messages from any chat other than `CHAT_ID` are dropped without being stored.
- `/inbox` routes need `Authorization: Bearer <INBOX_TOKEN>`. Secrets are compared in constant time.
- The Worker holds no bot token: it replies by answering the webhook with a `sendMessage` call.

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
