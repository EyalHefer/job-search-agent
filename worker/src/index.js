// Telegram inbox: the agent's mailbox in the cloud, and the bot I control it from on my phone.
//
// POST /telegram          Telegram calls this for every new message or button press (webhook).
//                         - a shared job post            -> saved for the agent
//                         - a WhatsApp chat export file  -> saved, replacing the group's older export
//                         - a reply to a digest job      -> saved as feedback (match and reason)
//                         - a button press on a job      -> saved as feedback (applied or not)
//                         - /run                         -> starts the agent on GitHub Actions
// GET  /inbox             The agent reads the waiting posts and feedback.
// POST /inbox/ack         The agent deletes the posts and feedback it has saved on disk.
// GET  /exports           The agent lists the saved chat exports.
// GET  /exports/file      The agent downloads one export: ?name=<file name>
//
// The Worker never reads WhatsApp. It only receives what I chose to share with the bot.

const POST = "msg:";
const EVENT = "ev:";
const EXPORT = "export:";
const KEEP_SECONDS = 30 * 24 * 60 * 60; // safety net: things the agent never collects expire
const MAX_FILE_BYTES = 20 * 1024 * 1024; // the largest file a bot may download from Telegram
const CHOSEN = " ✓";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const route = `${request.method} ${url.pathname}`;
    if (route === "POST /telegram") return receiveFromTelegram(request, env);
    if (!["GET /inbox", "POST /inbox/ack", "GET /exports", "GET /exports/file"].includes(route)) {
      return new Response("Not found", { status: 404 });
    }
    if (!authorized(request, env)) return unauthorized();
    if (route === "GET /inbox") return listInbox(env);
    if (route === "POST /inbox/ack") return ack(request, env);
    if (route === "GET /exports") return listExports(env);
    return getExport(url.searchParams.get("name"), env);
  },
};

async function receiveFromTelegram(request, env) {
  // Telegram sends the secret we gave it in setWebhook. Anyone else gets 401.
  if (!sameSecret(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), env.TELEGRAM_SECRET)) {
    return unauthorized();
  }
  const update = await request.json();
  if (update.callback_query) return receiveButton(update, env);

  const message = update.message;
  // Only messages from my own chat are kept. Others get 200 so Telegram does not send them again.
  if (!message || !isMine(message.chat, env)) return ok();
  const chatId = message.chat.id;

  if (message.text?.startsWith("/")) return command(message.text, chatId, env);
  const exportName = exportFileName(message.document);
  if (exportName) return receiveExport(message, exportName, env);
  if (message.reply_to_message?.from?.is_bot && message.text) return receiveReply(update, env);

  const text = message.text ?? message.caption;
  if (!text) return reply(chatId, refusal(message));

  // The key is the update_id, so a message Telegram sends twice is stored once.
  const record = { update_id: update.update_id, date: message.date, text };
  await env.INBOX.put(POST + update.update_id, JSON.stringify(record), { expirationTtl: KEEP_SECONDS });
  return reply(chatId, "נקלט ✓");
}

// ---- feedback on digest jobs ----

// The agent sends each digest job as its own message with buttons. A press tells which job by the
// message it is on; the agent knows which job it put in each message.
async function receiveButton(update, env) {
  const query = update.callback_query;
  const message = query.message;
  if (!message || !isMine(message.chat, env)) return ok();
  await saveEvent(env, update.update_id, {
    type: "button", message_id: message.message_id, choice: query.data, date: Math.floor(Date.now() / 1000),
  });
  // Mark the chosen button, so I can see what I picked. Pressing the other one changes my mind.
  if (message.reply_markup) {
    await callTelegram(env, "editMessageReplyMarkup", {
      chat_id: message.chat.id, message_id: message.message_id, reply_markup: markChosen(message.reply_markup, query.data),
    }).catch(() => {}); // the choice is saved either way
  }
  return Response.json({ method: "answerCallbackQuery", callback_query_id: query.id, text: "נשמר" });
}

// A reply to a digest job carries my own match and reason, for example "60 דורש 3 שנות ניסיון".
async function receiveReply(update, env) {
  const message = update.message;
  await saveEvent(env, update.update_id, {
    type: "reply", message_id: message.reply_to_message.message_id, text: message.text, date: message.date,
  });
  return reply(message.chat.id, "הפידבק נשמר ✓");
}

export function markChosen(markup, choice) {
  return {
    inline_keyboard: markup.inline_keyboard.map((row) => row.map((button) => {
      const text = button.text.endsWith(CHOSEN) ? button.text.slice(0, -CHOSEN.length) : button.text;
      return { ...button, text: button.callback_data === choice ? text + CHOSEN : text };
    })),
  };
}

function saveEvent(env, updateId, event) {
  const record = JSON.stringify({ update_id: updateId, ...event });
  return env.INBOX.put(EVENT + updateId, record, { expirationTtl: KEEP_SECONDS });
}

// ---- chat exports ----

// The name to keep a shared file under, or null when it is not a chat export. Phones do not always
// keep the extension, so the file's type counts too; the agent's reader needs .zip or .txt.
export function exportFileName(document) {
  if (!document) return null;
  const name = cleanName(document.file_name ?? "");
  if (/\.(zip|txt)$/i.test(name)) return name;
  const type = document.mime_type ?? "";
  if (/zip/i.test(type)) return (name || "export") + ".zip";
  if (/^text\/plain/i.test(type)) return (name || "export") + ".txt";
  return null;
}

// Says what arrived, so I can tell why it was not taken.
function refusal(message) {
  const document = message.document;
  const what = document
    ? `קיבלתי קובץ "${document.file_name ?? "בלי שם"}" מסוג ${document.mime_type ?? "לא ידוע"}.`
    : `קיבלתי הודעה מסוג ${Object.keys(message).find((k) => MEDIA.includes(k)) ?? "לא ידוע"}.`;
  return `${what}\nרק טקסט או קובץ ייצוא של צ'אט (zip או txt) נקלטים.`;
}

const MEDIA = ["photo", "video", "audio", "voice", "sticker", "animation", "contact", "location", "poll"];

// Exports of the groups that allow it, shared from the phone instead of sent by email.
// A newer export of the same group has the same file name and replaces the older one.
async function receiveExport(message, name, env) {
  const chatId = message.chat.id;
  const document = message.document;
  if (document.file_size > MAX_FILE_BYTES) return reply(chatId, "הקובץ גדול מדי. ייצא את הצ'אט בלי מדיה.");
  try {
    const file = await callTelegram(env, "getFile", { file_id: document.file_id });
    const response = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${file.file_path}`);
    if (!response.ok) throw new Error(`download failed: HTTP ${response.status}`);
    const bytes = await response.arrayBuffer();
    await env.INBOX.put(EXPORT + name, bytes, {
      expirationTtl: KEEP_SECONDS, metadata: { received_at: message.date, size: bytes.byteLength },
    });
  } catch {
    return reply(chatId, "לא הצלחתי לשמור את הקובץ. נסה לשלוח אותו שוב.");
  }
  return reply(chatId, `הייצוא נשמר ✓\n${name}`);
}

// Keeps only the last part of the name, so it cannot point outside the agent's exports folder.
export function safeFileName(name) {
  return cleanName(name) || "export.zip";
}

function cleanName(name) {
  return String(name).split(/[\\/]/).pop().replace(/[\u0000-\u001f]/g, "").replace(/^\.+/, "").slice(-200);
}

async function listExports(env) {
  const exports = [];
  await eachKey(env, EXPORT, ({ name, metadata }) => {
    exports.push({ name: name.slice(EXPORT.length), ...metadata });
  });
  return Response.json({ exports });
}

async function getExport(name, env) {
  const bytes = name ? await env.INBOX.get(EXPORT + name, "arrayBuffer") : null;
  if (!bytes) return new Response("Not found", { status: 404 });
  return new Response(bytes, { headers: { "Content-Type": "application/octet-stream" } });
}

// ---- commands ----

const HELP = [
  "מה אפשר לשלוח לי:",
  "• הודעות משרה מוואטסאפ (סמן ← שתף) – נשמרות לריצה הבאה",
  "• ייצוא צ'אט של קבוצה (ייצוא צ'אט ← בלי מדיה ← טלגרם)",
  "• /run – מפעיל את הסוכן. הדיגסט יגיע לכאן",
  "• על משרה בדיגסט: כפתור, או תשובה להודעה עם התאמה והסבר, למשל: 60 דורש 3 שנות ניסיון",
].join("\n");

async function command(text, chatId, env) {
  const name = text.split(/[\s@]/)[0];
  if (name !== "/run") return reply(chatId, HELP);
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return reply(chatId, "ההפעלה מהטלפון עוד לא הוגדרה.");
  const workflow = env.GITHUB_WORKFLOW ?? "run.yml";
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "telegram-inbox-worker",
    },
    body: JSON.stringify({ ref: "main" }),
  }).catch(() => null);
  if (!response?.ok) {
    return reply(chatId, `לא הצלחתי להפעיל את הסוכן (${response ? "HTTP " + response.status : "אין חיבור"}).`);
  }
  return reply(chatId, "הסוכן התחיל לרוץ. הדיגסט יגיע לכאן בעוד כ-10 דקות.");
}

// ---- the agent's routes ----

async function listInbox(env) {
  const read = async (prefix) => {
    const records = [];
    await eachKey(env, prefix, async ({ name }) => {
      const value = await env.INBOX.get(name);
      if (value) records.push(JSON.parse(value));
    });
    return records.sort((a, b) => a.update_id - b.update_id);
  };
  return Response.json({ messages: await read(POST), events: await read(EVENT) });
}

async function ack(request, env) {
  const body = await request.json().catch(() => null);
  const ids = body?.update_ids;
  if (!Array.isArray(ids) || !ids.every(Number.isInteger)) {
    return Response.json({ error: "expected {\"update_ids\": [integers]}" }, { status: 400 });
  }
  // update_ids are unique across posts and feedback, so both kinds can be deleted by id.
  await Promise.all(ids.flatMap((id) => [env.INBOX.delete(POST + id), env.INBOX.delete(EVENT + id)]));
  return Response.json({ deleted: ids.length });
}

async function eachKey(env, prefix, visit) {
  let cursor;
  do {
    const page = await env.INBOX.list({ prefix, cursor });
    for (const key of page.keys) await visit(key);
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
}

// ---- helpers ----

function isMine(chat, env) {
  return String(chat?.id) === String(env.CHAT_ID);
}

async function callTelegram(env, method, params) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const body = await response.json().catch(() => ({}));
  if (!body.ok) throw new Error(`${method} failed`);
  return body.result;
}

// Answering the webhook with a method call makes Telegram send the reply without another request.
function reply(chatId, text) {
  return Response.json({ method: "sendMessage", chat_id: chatId, text });
}

function ok() {
  return new Response(null, { status: 200 });
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
