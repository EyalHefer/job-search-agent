import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, { exportFileName, markChosen, nameFromContent, safeFileName, sameSecret } from "../src/index.js";

// An in-memory stand-in for Cloudflare KV, with the same methods the Worker uses.
class FakeKV {
  constructor() {
    this.data = new Map();
  }
  async put(key, value, options = {}) {
    this.data.set(key, { value, metadata: options.metadata });
  }
  async get(key) {
    return this.data.get(key)?.value ?? null; // stores values as given, so "arrayBuffer" reads need nothing more
  }
  async delete(key) {
    this.data.delete(key);
  }
  async list({ prefix, cursor }) {
    const names = [...this.data.keys()].filter((k) => k.startsWith(prefix)).sort();
    const start = cursor ? Number(cursor) : 0;
    const page = names.slice(start, start + 2); // tiny pages, to exercise the cursor loop
    const done = start + 2 >= names.length;
    return { keys: page.map((name) => ({ name, metadata: this.data.get(name).metadata })), list_complete: done, cursor: done ? undefined : String(start + 2) };
  }
}

const MY_CHAT = 1000;
let env;

beforeEach(() => {
  env = {
    INBOX: new FakeKV(), TELEGRAM_SECRET: "tg-secret", INBOX_TOKEN: "inbox-token", CHAT_ID: String(MY_CHAT),
    TELEGRAM_BOT_TOKEN: "bot-token", GITHUB_TOKEN: "gh-token", GITHUB_REPO: "me/private",
  };
});

function fromTelegram(update, secret = "tg-secret") {
  return worker.fetch(
    new Request("https://w.example/telegram", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": secret, "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
    env,
  );
}

function update(id, text, chat = MY_CHAT) {
  return { update_id: id, message: { message_id: id, date: 1795000000 + id, chat: { id: chat }, text } };
}

function agent(method, path, body, token = "inbox-token") {
  return worker.fetch(
    new Request(`https://w.example${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    env,
  );
}

describe("POST /telegram", () => {
  it("rejects a request without Telegram's secret", async () => {
    expect((await fromTelegram(update(1, "job"), "wrong")).status).toBe(401);
    expect(env.INBOX.data.size).toBe(0);
  });

  it("stores my message and answers with a confirmation", async () => {
    const res = await fromTelegram(update(1, "*Junior QA* / Acme https://acme.example/1"));
    expect(await res.json()).toEqual({ method: "sendMessage", chat_id: MY_CHAT, text: "נקלט ✓" });
    expect(env.INBOX.data.size).toBe(1);
  });

  it("ignores messages from other chats without storing them", async () => {
    const res = await fromTelegram(update(1, "spam", 2000));
    expect(res.status).toBe(200);
    expect(env.INBOX.data.size).toBe(0);
  });

  it("stores an update Telegram sends twice only once", async () => {
    await fromTelegram(update(7, "job"));
    await fromTelegram(update(7, "job"));
    expect(env.INBOX.data.size).toBe(1);
  });

  it("tells me when a message has no text", async () => {
    const res = await fromTelegram({ update_id: 3, message: { message_id: 3, date: 1, chat: { id: MY_CHAT } } });
    expect((await res.json()).text).toMatch(/רק טקסט/);
    expect(env.INBOX.data.size).toBe(0);
  });
});

describe("GET /inbox and POST /inbox/ack", () => {
  it("require the agent's token", async () => {
    expect((await agent("GET", "/inbox", undefined, "wrong")).status).toBe(401);
    expect((await agent("POST", "/inbox/ack", { update_ids: [1] }, "wrong")).status).toBe(401);
  });

  it("list every waiting message in order, across pages", async () => {
    for (const id of [3, 1, 2, 4, 5]) await fromTelegram(update(id, `job ${id}`));
    const { messages } = await (await agent("GET", "/inbox")).json();
    expect(messages.map((m) => m.update_id)).toEqual([1, 2, 3, 4, 5]);
    expect(messages[0]).toEqual({ update_id: 1, date: 1795000001, text: "job 1" });
  });

  it("ack deletes only the messages it names", async () => {
    for (const id of [1, 2, 3]) await fromTelegram(update(id, `job ${id}`));
    await agent("POST", "/inbox/ack", { update_ids: [1, 3] });
    const { messages } = await (await agent("GET", "/inbox")).json();
    expect(messages.map((m) => m.update_id)).toEqual([2]);
  });

  it("ack rejects a malformed body", async () => {
    expect((await agent("POST", "/inbox/ack", { update_ids: ["1"] })).status).toBe(400);
  });
});

it("unknown routes are 404", async () => {
  expect((await agent("GET", "/")).status).toBe(404);
});

it("sameSecret needs an exact, non-empty match", () => {
  expect(sameSecret("abc", "abc")).toBe(true);
  expect(sameSecret("abd", "abc")).toBe(false);
  expect(sameSecret("abcd", "abc")).toBe(false);
  expect(sameSecret("", "")).toBe(false);
  expect(sameSecret(null, "abc")).toBe(false);
});

// ---- outside calls (Telegram's API and GitHub's) are answered by a fake fetch ----

let calls;
function fakeFetch(handler) {
  calls = [];
  vi.stubGlobal("fetch", async (url, init = {}) => {
    calls.push({ url: String(url), init });
    return handler(String(url), init);
  });
}
afterEach(() => vi.unstubAllGlobals());

const BUTTONS = { inline_keyboard: [[{ text: "✅ הגשתי", callback_data: "applied" }, { text: "❌ לא הגשתי", callback_data: "skipped" }]] };

function press(id, choice, chat = MY_CHAT) {
  return fromTelegram({
    update_id: id,
    callback_query: { id: "q" + id, data: choice, message: { message_id: 55, date: 1, chat: { id: chat }, reply_markup: BUTTONS } },
  });
}

describe("feedback on digest jobs", () => {
  it("a button press is saved with the message it was on, and the button is marked", async () => {
    fakeFetch(() => Response.json({ ok: true, result: true }));
    const res = await press(10, "applied");
    expect(await res.json()).toEqual({ method: "answerCallbackQuery", callback_query_id: "q10", text: "נשמר" });
    const { events } = await (await agent("GET", "/inbox")).json();
    expect(events).toMatchObject([{ update_id: 10, type: "button", message_id: 55, choice: "applied" }]);
    const edit = JSON.parse(calls[0].init.body);
    expect(calls[0].url).toBe("https://api.telegram.org/botbot-token/editMessageReplyMarkup");
    expect(edit.reply_markup.inline_keyboard[0].map((b) => b.text)).toEqual(["✅ הגשתי ✓", "❌ לא הגשתי"]);
  });

  it("the choice is saved even when marking the button fails", async () => {
    fakeFetch(() => new Response("down", { status: 502 }));
    expect((await press(11, "skipped")).status).toBe(200);
    const { events } = await (await agent("GET", "/inbox")).json();
    expect(events).toHaveLength(1);
  });

  it("presses from other chats are ignored", async () => {
    fakeFetch(() => Response.json({ ok: true }));
    await press(12, "applied", 2000);
    expect(env.INBOX.data.size).toBe(0);
  });

  it("a reply to a bot message is feedback, not a job post", async () => {
    const res = await fromTelegram({
      update_id: 13,
      message: { message_id: 60, date: 5, chat: { id: MY_CHAT }, text: "60 דורש 3 שנות ניסיון",
                 reply_to_message: { message_id: 55, from: { is_bot: true } } },
    });
    expect((await res.json()).text).toBe("הפידבק נשמר ✓");
    const { messages, events } = await (await agent("GET", "/inbox")).json();
    expect(messages).toEqual([]);
    expect(events).toEqual([{ update_id: 13, type: "reply", message_id: 55, text: "60 דורש 3 שנות ניסיון", date: 5 }]);
  });

  it("ack deletes feedback too", async () => {
    fakeFetch(() => Response.json({ ok: true }));
    await press(14, "applied");
    await fromTelegram(update(15, "job"));
    await agent("POST", "/inbox/ack", { update_ids: [14, 15] });
    expect(env.INBOX.data.size).toBe(0);
  });

  it("markChosen moves the mark to the new choice", () => {
    const once = markChosen(BUTTONS, "applied");
    const twice = markChosen(once, "skipped");
    expect(twice.inline_keyboard[0].map((b) => b.text)).toEqual(["✅ הגשתי", "❌ לא הגשתי ✓"]);
  });
});

function shareFile(id, file_name, file_size = 1000) {
  return fromTelegram({
    update_id: id,
    message: { message_id: id, date: 1795000000, chat: { id: MY_CHAT }, document: { file_id: "f" + id, file_name, file_size } },
  });
}

describe("chat exports", () => {
  const telegramFiles = (url) => url.includes("/getFile")
    ? Response.json({ ok: true, result: { file_path: "documents/file_1.zip" } })
    : new Response(new Uint8Array([80, 75, 3, 4]));

  it("an export file is downloaded from Telegram and kept under its name", async () => {
    fakeFetch(telegramFiles);
    const res = await shareFile(20, "צ'אט WhatsApp עם Jobs.zip");
    expect((await res.json()).text).toMatch(/הייצוא נשמר/);
    expect(calls[1].url).toBe("https://api.telegram.org/file/botbot-token/documents/file_1.zip");

    const { exports } = await (await agent("GET", "/exports")).json();
    expect(exports).toEqual([{ name: "צ'אט WhatsApp עם Jobs.zip", received_at: 1795000000, size: 4 }]);
    const file = await agent("GET", "/exports/file?name=" + encodeURIComponent("צ'אט WhatsApp עם Jobs.zip"));
    expect([...new Uint8Array(await file.arrayBuffer())]).toEqual([80, 75, 3, 4]);
  });

  it("a newer export of the same group replaces the older one", async () => {
    fakeFetch(telegramFiles);
    await shareFile(21, "Jobs.txt");
    await shareFile(22, "Jobs.txt");
    expect((await (await agent("GET", "/exports")).json()).exports).toHaveLength(1);
  });

  it("files too large for a bot are refused before downloading", async () => {
    fakeFetch(telegramFiles);
    const res = await shareFile(23, "big.zip", 50 * 1024 * 1024);
    expect((await res.json()).text).toMatch(/גדול מדי/);
    expect(calls).toHaveLength(0);
  });

  it("tells me when the download fails, and stores nothing", async () => {
    fakeFetch(() => new Response("no", { status: 500 }));
    expect((await (await shareFile(24, "a.zip")).json()).text).toMatch(/לא הצלחתי/);
    expect(env.INBOX.data.size).toBe(0);
  });

  it("export routes need the agent's token, and unknown names are 404", async () => {
    expect((await agent("GET", "/exports", undefined, "wrong")).status).toBe(401);
    expect((await agent("GET", "/exports/file?name=nope.zip")).status).toBe(404);
  });

  it("file names cannot leave the exports folder", () => {
    expect(safeFileName("../../etc/x.zip")).toBe("x.zip");
    expect(safeFileName(String.raw`a\..zip`)).toBe("zip");
    expect(safeFileName("..")).toBe("export.zip");
  });
});

describe("/run", () => {
  it("starts the workflow on GitHub", async () => {
    fakeFetch(() => new Response(null, { status: 204 }));
    const res = await fromTelegram(update(30, "/run"));
    expect((await res.json()).text).toMatch(/התחיל לרוץ/);
    expect(calls[0].url).toBe("https://api.github.com/repos/me/private/actions/workflows/run.yml/dispatches");
    expect(calls[0].init.headers.Authorization).toBe("Bearer gh-token");
    expect(JSON.parse(calls[0].init.body)).toEqual({ ref: "main" });
    expect(env.INBOX.data.size).toBe(0); // commands are not job posts
  });

  it("says so when GitHub refuses", async () => {
    fakeFetch(() => new Response(null, { status: 403 }));
    expect((await (await fromTelegram(update(31, "/run"))).json()).text).toMatch(/HTTP 403/);
  });

  it("other commands show the help", async () => {
    expect((await (await fromTelegram(update(32, "/start"))).json()).text).toMatch(/\/run/);
  });
});

describe("recognizing shared files", () => {
  it("a file without an extension is taken by its type", () => {
    expect(exportFileName({ file_name: "WhatsApp Chat - Jobs", mime_type: "application/zip" })).toBe("WhatsApp Chat - Jobs.zip");
    expect(exportFileName({ file_name: "Jobs", mime_type: "text/plain" })).toBe("Jobs.txt");
    expect(exportFileName({ mime_type: "application/x-zip-compressed" })).toBe("export.zip");
    expect(exportFileName({ file_name: "Jobs.TXT", mime_type: "application/octet-stream" })).toBe("Jobs.TXT");
  });

  it("other files are refused, with what arrived", async () => {
    expect(exportFileName({ file_name: "cv.pdf", mime_type: "application/pdf" })).toBe(null);
    const res = await fromTelegram({
      update_id: 40,
      message: { message_id: 40, date: 1, chat: { id: MY_CHAT }, document: { file_id: "x", file_name: "cv.pdf", mime_type: "application/pdf" } },
    });
    const { text } = await res.json();
    expect(text).toContain('"cv.pdf"');
    expect(text).toContain("application/pdf");
    expect(env.INBOX.data.size).toBe(0);
  });

  it("a photo is refused and named as one", async () => {
    const res = await fromTelegram({ update_id: 41, message: { message_id: 41, date: 1, chat: { id: MY_CHAT }, photo: [{}] } });
    expect((await res.json()).text).toContain("photo");
  });
});

describe("files with no extension and a generic type", () => {
  function shareBlob(id, content) {
    fakeFetch((url) => url.includes("/getFile")
      ? Response.json({ ok: true, result: { file_path: "documents/f" } })
      : new Response(content));
    return fromTelegram({
      update_id: id,
      message: { message_id: id, date: 1795000000, chat: { id: MY_CHAT },
                 document: { file_id: "f", file_name: "Devops קבוצה 2", mime_type: "application/octet-stream", file_size: 10 } },
    });
  }

  it("a zip is recognized by its first bytes", async () => {
    expect((await (await shareBlob(50, new Uint8Array([0x50, 0x4b, 3, 4, 0]))).json()).text).toMatch(/הייצוא נשמר/);
    expect((await (await agent("GET", "/exports")).json()).exports[0].name).toBe("Devops קבוצה 2.zip");
  });

  it("a text export is recognized by its first chat line", async () => {
    await shareBlob(51, "28.10.2026, 14:03 - +972 50-000-0000: *QA* https://x.example/1\n");
    expect((await (await agent("GET", "/exports")).json()).exports[0].name).toBe("Devops קבוצה 2.txt");
  });

  it("anything else is refused and not stored", async () => {
    const { text } = await (await shareBlob(52, "%PDF-1.7 not a chat")).json();
    expect(text).toContain("application/octet-stream");
    expect(env.INBOX.data.size).toBe(0);
  });

  it("nameFromContent", () => {
    const bytes = (s) => new TextEncoder().encode(s).buffer;
    expect(nameFromContent(undefined, bytes("[18.11.26, 12:29:01] ~ Dana: hi"))).toBe("export.txt");
    expect(nameFromContent("x", bytes("hello"))).toBe(null);
  });
});
