// Telegram inbox: a mailbox in the cloud for job posts I share to my private Telegram bot.
//
// POST /telegram     Telegram calls this for every new message (webhook). Saves the text in KV.
// GET  /inbox        The agent reads the waiting messages.
// POST /inbox/ack    The agent deletes the messages it has saved on disk.
//
// The Worker never reads WhatsApp. It only receives what I chose to share with the bot.

const KEY_PREFIX = "msg:";
const KEEP_SECONDS = 30 * 24 * 60 * 60; // safety net: messages the agent never collects expire

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    const route = `${request.method} ${pathname}`;
    if (route === "POST /telegram") return receiveFromTelegram(request, env);
    if (route === "GET /inbox") return authorized(request, env) ? listInbox(env) : unauthorized();
    if (route === "POST /inbox/ack") return authorized(request, env) ? ack(request, env) : unauthorized();
    return new Response("Not found", { status: 404 });
  },
};

async function receiveFromTelegram(request, env) {
  // Telegram sends the secret we gave it in setWebhook. Anyone else gets 401.
  if (!sameSecret(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), env.TELEGRAM_SECRET)) {
    return unauthorized();
  }
  const update = await request.json();
  const message = update.message;
  // Only messages from my own chat are kept. Others get 200 so Telegram does not send them again.
  if (!message || String(message.chat?.id) !== String(env.CHAT_ID)) return new Response(null, { status: 200 });

  const text = message.text ?? message.caption;
  if (!text) return reply(message.chat.id, "רק טקסט נקלט. שתף את טקסט ההודעה.");

  // The key is the update_id, so a message Telegram sends twice is stored once.
  const record = { update_id: update.update_id, date: message.date, text };
  await env.INBOX.put(KEY_PREFIX + update.update_id, JSON.stringify(record), { expirationTtl: KEEP_SECONDS });
  return reply(message.chat.id, "נקלט ✓");
}

async function listInbox(env) {
  const messages = [];
  let cursor;
  do {
    const page = await env.INBOX.list({ prefix: KEY_PREFIX, cursor });
    for (const { name } of page.keys) {
      const value = await env.INBOX.get(name);
      if (value) messages.push(JSON.parse(value));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  messages.sort((a, b) => a.update_id - b.update_id);
  return Response.json({ messages });
}

async function ack(request, env) {
  const body = await request.json().catch(() => null);
  const ids = body?.update_ids;
  if (!Array.isArray(ids) || !ids.every(Number.isInteger)) {
    return Response.json({ error: "expected {\"update_ids\": [integers]}" }, { status: 400 });
  }
  await Promise.all(ids.map((id) => env.INBOX.delete(KEY_PREFIX + id)));
  return Response.json({ deleted: ids.length });
}

// Answering the webhook with a method call makes Telegram send the reply, so the Worker needs no bot token.
function reply(chatId, text) {
  return Response.json({ method: "sendMessage", chat_id: chatId, text });
}

function authorized(request, env) {
  const header = request.headers.get("Authorization") ?? "";
  return header.startsWith("Bearer ") && sameSecret(header.slice("Bearer ".length), env.INBOX_TOKEN);
}

function unauthorized() {
  return new Response("Unauthorized", { status: 401 });
}

// Compares in time that does not depend on where the strings differ, so the secret cannot be guessed
// character by character from response times.
export function sameSecret(given, expected) {
  if (typeof given !== "string" || typeof expected !== "string" || expected.length === 0) return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  let diff = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) diff |= (a[i] ?? 0) ^ b[i];
  return diff === 0;
}
