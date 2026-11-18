import { beforeEach, describe, expect, it } from "vitest";
import worker, { sameSecret } from "../src/index.js";

// An in-memory stand-in for Cloudflare KV, with the same methods the Worker uses.
class FakeKV {
  constructor() {
    this.data = new Map();
  }
  async put(key, value) {
    this.data.set(key, value);
  }
  async get(key) {
    return this.data.get(key) ?? null;
  }
  async delete(key) {
    this.data.delete(key);
  }
  async list({ prefix, cursor }) {
    const names = [...this.data.keys()].filter((k) => k.startsWith(prefix)).sort();
    const start = cursor ? Number(cursor) : 0;
    const page = names.slice(start, start + 2); // tiny pages, to exercise the cursor loop
    const done = start + 2 >= names.length;
    return { keys: page.map((name) => ({ name })), list_complete: done, cursor: done ? undefined : String(start + 2) };
  }
}

const MY_CHAT = 1000;
let env;

beforeEach(() => {
  env = { INBOX: new FakeKV(), TELEGRAM_SECRET: "tg-secret", INBOX_TOKEN: "inbox-token", CHAT_ID: String(MY_CHAT) };
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
