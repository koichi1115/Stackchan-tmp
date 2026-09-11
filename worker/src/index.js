// stackchan-inbox — Grok が投函し、常時起動 Mac のリレーが取り出すテキスト専用の受信箱。
//
// 依存なしの ES モジュール Worker。データは D1（binding: DB）の messages テーブル。
//
//   POST /messages              送信キー  {"text":"…"} → 201 {"id":n}
//   GET  /messages?after&limit  巡回キー  未読を古い順に → 200 {"messages":[{"id","text","created_at"}]}
//   POST /messages/{id}/ack     巡回キー  読み上げ済みにする → 200 {"id":n,"acked":true} / 404
//   GET  /healthz               誰でも    "ok"
//
// 送信キー（INBOX_SEND_KEY）と巡回キー（INBOX_POLL_KEY）は Worker の secret。
// どちらかが未設定なら全経路 503 を返し、設定途中の配置が開いたままにならないようにする。

const MAX_BODY_BYTES = 8 * 1024;
const MAX_TEXT_CHARS = 500;
const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 50;

const encoder = new TextEncoder();
const utf8 = new TextDecoder("utf-8", { fatal: true });

// 空白へ置き換えた後に残る制御文字・不可視文字（C0/C1、ゼロ幅、双方向制御、BOM）
const CONTROL_CHARS =
	/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/g;

export default {
	async fetch(request, env) {
		try {
			return await route(request, env);
		} catch (error) {
			console.error("unhandled error", error instanceof Error ? error.message : String(error));
			return json(500, { error: "internal error" });
		}
	},
};

async function route(request, env) {
	const url = new URL(request.url);
	const path = url.pathname;
	const method = request.method;

	if (!isConfigured(env)) {
		if (path === "/healthz") {
			return text(503, "not configured");
		}
		return json(503, { error: "not configured" });
	}

	if (method === "GET" && path === "/healthz") {
		return text(200, "ok");
	}

	if (path === "/messages") {
		if (method === "POST") {
			return withKey(request, env.INBOX_SEND_KEY, () => postMessage(request, env));
		}
		if (method === "GET") {
			return withKey(request, env.INBOX_POLL_KEY, () => listMessages(url, env));
		}
	}

	const ack = /^\/messages\/(\d{1,15})\/ack$/.exec(path);
	if (ack && method === "POST") {
		return withKey(request, env.INBOX_POLL_KEY, () => ackMessage(Number(ack[1]), env));
	}

	return json(404, { error: "not found" });
}

function isConfigured(env) {
	return (
		typeof env.INBOX_SEND_KEY === "string" && env.INBOX_SEND_KEY.trim() !== "" &&
		typeof env.INBOX_POLL_KEY === "string" && env.INBOX_POLL_KEY.trim() !== "" &&
		env.DB !== undefined
	);
}

// --- 認証 -------------------------------------------------------------------

async function withKey(request, expectedKey, handler) {
	const presented = bearerToken(request.headers.get("authorization"));
	if (presented === null || !(await keysMatch(presented, expectedKey))) {
		return json(401, { error: "unauthorized" }, { "www-authenticate": "Bearer" });
	}
	return handler();
}

function bearerToken(header) {
	if (!header) {
		return null;
	}
	const match = /^\s*Bearer\s+(\S+)\s*$/i.exec(header);
	return match ? match[1] : null;
}

// 両方を SHA-256 にしてから固定長で比較する。長さの違いや一致した先頭の長さで所要時間が変わらない。
async function keysMatch(a, b) {
	const [da, db] = await Promise.all([digest(a), digest(b)]);
	let diff = 0;
	for (let i = 0; i < da.length; i++) {
		diff |= da[i] ^ db[i];
	}
	return diff === 0;
}

async function digest(value) {
	return new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
}

// --- 経路 -------------------------------------------------------------------

async function postMessage(request, env) {
	const body = await readBody(request, MAX_BODY_BYTES);
	if (body === null) {
		return json(413, { error: "body too large" });
	}

	let parsed;
	try {
		parsed = JSON.parse(utf8.decode(body));
	} catch {
		return json(400, { error: "invalid json" });
	}
	if (parsed === null || typeof parsed !== "object" || typeof parsed.text !== "string") {
		return json(400, { error: "text (string) is required" });
	}

	const cleaned = sanitizeText(parsed.text);
	if (cleaned === "") {
		return json(400, { error: "text is empty" });
	}

	const row = await env.DB
		.prepare("INSERT INTO messages (text) VALUES (?) RETURNING id")
		.bind(cleaned)
		.first();
	return json(201, { id: row.id });
}

async function listMessages(url, env) {
	const after = parseNonNegativeInt(url.searchParams.get("after"), 0);
	const requestedLimit = parseNonNegativeInt(url.searchParams.get("limit"), DEFAULT_LIMIT);
	if (after === null || requestedLimit === null) {
		return json(400, { error: "after and limit must be non-negative integers" });
	}
	const limit = Math.min(Math.max(requestedLimit, 1), MAX_LIMIT);

	const result = await env.DB
		.prepare(
			"SELECT id, text, created_at FROM messages " +
			"WHERE delivered_at IS NULL AND id > ? ORDER BY id ASC LIMIT ?",
		)
		.bind(after, limit)
		.all();

	const messages = result.results.map((row) => ({
		id: row.id,
		text: row.text,
		created_at: row.created_at,
	}));
	return json(200, { messages });
}

async function ackMessage(id, env) {
	const update = await env.DB
		.prepare(
			"UPDATE messages SET delivered_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') " +
			"WHERE id = ? AND delivered_at IS NULL",
		)
		.bind(id)
		.run();
	if (update.meta.changes > 0) {
		return json(200, { id, acked: true });
	}

	// 変更が無かったのは「存在しない」か「既に ack 済み」。後者は同じ応答を返す（再送しても害がない）。
	const existing = await env.DB.prepare("SELECT id FROM messages WHERE id = ?").bind(id).first();
	if (!existing) {
		return json(404, { error: "not found" });
	}
	return json(200, { id, acked: true });
}

// --- 入力の整形 ---------------------------------------------------------------

// 改行・タブなどの空白類を一つの空白にまとめ、残った制御文字を除き、前後を詰めて 500 文字で切る。
function sanitizeText(raw) {
	const collapsed = raw
		.replace(/\s+/g, " ")
		.replace(CONTROL_CHARS, "")
		.replace(/ {2,}/g, " ")
		.trim();
	// サロゲートペアを分断しないよう、コードポイント単位で切る
	return Array.from(collapsed).slice(0, MAX_TEXT_CHARS).join("").trim();
}

function parseNonNegativeInt(value, fallback) {
	if (value === null || value === "") {
		return fallback;
	}
	if (!/^\d{1,15}$/.test(value)) {
		return null;
	}
	return Number(value);
}

// 本文を上限まで読む。超えたら null（読み込みを打ち切る）。
async function readBody(request, limit) {
	const declared = request.headers.get("content-length");
	if (declared !== null && Number(declared) > limit) {
		return null;
	}
	if (!request.body) {
		return new Uint8Array(0);
	}

	const reader = request.body.getReader();
	const chunks = [];
	let total = 0;
	for (;;) {
		const { done, value } = await reader.read();
		if (done) {
			break;
		}
		total += value.byteLength;
		if (total > limit) {
			await reader.cancel();
			return null;
		}
		chunks.push(value);
	}

	const out = new Uint8Array(total);
	let offset = 0;
	for (const chunk of chunks) {
		out.set(chunk, offset);
		offset += chunk.byteLength;
	}
	return out;
}

// --- 応答 -------------------------------------------------------------------

function json(status, body, extraHeaders = {}) {
	return new Response(JSON.stringify(body), {
		status,
		headers: {
			"content-type": "application/json; charset=utf-8",
			"cache-control": "no-store",
			"x-content-type-options": "nosniff",
			...extraHeaders,
		},
	});
}

function text(status, body) {
	return new Response(body, {
		status,
		headers: {
			"content-type": "text/plain; charset=utf-8",
			"cache-control": "no-store",
			"x-content-type-options": "nosniff",
		},
	});
}
