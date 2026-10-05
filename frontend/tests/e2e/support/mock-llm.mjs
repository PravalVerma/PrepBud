/**
 * Minimal OpenAI-compatible LLM server for E2E runs and local smoke tests — the backend
 * talks to it through its normal provider path (LLM_BASE_URL), which also demonstrates
 * AC-3.7: any OpenAI-compatible endpoint works via configuration.
 *
 *   node tests/e2e/support/mock-llm.mjs   (PORT, default 54330)
 *
 * POST /v1/chat/completions
 *   - concept extraction prompts: reads lines
 *       "Concept: <name> | <description> [| requires: A, B] | end"
 *     inside each ---CHUNK n--- block and answers with the requested JSON;
 *     without such markers it derives one concept per chunk from its heading/words.
 *   - relationship prompts: answers prerequisite edges for the "requires" seen so far.
 *   - session prompts (by their "## Task: …" heading): MCQ question generation (option A is
 *     correct), answer evaluation, misconception diagnosis, a plain-text session summary;
 *     `stream: true` requests (tutor explanations) stream word by word as SSE.
 * POST /v1/embeddings  — deterministic hashed bag-of-words vectors (`dimensions`, default 1536).
 */
import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 54330);
const CONCEPT = /Concept:\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(?:requires:\s*([^|]*?)\s*\|\s*)?end\b/g;
const CHUNK = /---CHUNK (\d+)---/;
/** normalised concept name → Set of normalised prerequisite names */
const requires = new Map();

const norm = (s) => s.toLowerCase().replace(/[^\w\s+#]/g, " ").split(/\s+/).filter(Boolean).join(" ");

function extract(prompt) {
  const parts = prompt.split(CHUNK);
  const concepts = new Map();
  for (let i = 1; i < parts.length; i += 2) {
    const number = Number(parts[i]);
    const body = parts[i + 1].split("---END OF MATERIAL---")[0];
    let found = false;
    for (const m of body.matchAll(CONCEPT)) {
      found = true;
      const name = m[1].replace(/\s+/g, " ");
      const entry = concepts.get(name) ?? {
        name,
        description: m[2].replace(/\s+/g, " "),
        difficulty_estimate: "medium",
        prerequisites: [],
        relationships: [],
        chunks: [],
      };
      entry.chunks.push(number);
      for (const req of (m[3] ?? "").split(",").map((r) => r.trim()).filter(Boolean)) {
        if (!entry.prerequisites.includes(req)) entry.prerequisites.push(req);
        if (!requires.has(norm(name))) requires.set(norm(name), new Set());
        requires.get(norm(name)).add(norm(req));
      }
      concepts.set(name, entry);
    }
    if (!found) {
      // Generic material: one concept named after the section heading or first words.
      const heading = (body.match(/\(section: ([^)]+)\)/) ?? [])[1];
      const words = body.replace(/[^A-Za-z ]/g, " ").split(/\s+/).filter((w) => w.length > 3);
      const name = heading ?? words.slice(0, 3).join(" ");
      if (name && !concepts.has(name)) {
        concepts.set(name, {
          name,
          description: words.slice(0, 20).join(" "),
          difficulty_estimate: "medium",
          prerequisites: [],
          relationships: [],
          chunks: [number],
        });
      }
    }
  }
  return { concepts: [...concepts.values()] };
}

function relate(prompt) {
  const refs = new Map();
  for (const m of prompt.matchAll(/^- ([NE]\d+): (.+?)(?: — .*)?$/gm)) refs.set(m[1], m[2].trim());
  const byKey = new Map();
  for (const [ref, name] of refs) if (!byKey.has(norm(name))) byKey.set(norm(name), ref);
  const relationships = [];
  for (const [ref, name] of refs) {
    if (!ref.startsWith("N")) continue;
    for (const req of requires.get(norm(name)) ?? []) {
      const source = byKey.get(req);
      if (source) relationships.push({ source, target: ref, type: "prerequisite", strength: 0.9 });
    }
  }
  return { duplicates: [], relationships };
}

function embed(text, dims) {
  const vec = new Array(dims).fill(0);
  for (const word of text.toLowerCase().match(/[a-z0-9]+/g) ?? []) {
    if (word.length < 3) continue;
    let h = 2166136261;
    for (const ch of word) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
    vec[h % dims] += 1;
  }
  const norm2 = Math.sqrt(vec.reduce((a, v) => a + v * v, 0)) || 1;
  return vec.map((v) => v / norm2);
}

const tokens = (s) => Math.ceil(s.length / 4);

// --- learning sessions (prompts are identified by their "## Task: …" heading) --------------

let questionCounter = 0;

/** Tutor text for streamed tasks — markdown with inline and display LaTeX (AC-5.6). */
function tutorText(task, prompt) {
  const concept = (prompt.match(/Teach "([^"]+)"/) ?? prompt.match(/Concept: (.+)/) ?? [])[1] ?? "this idea";
  if (task === "answer a follow-up question") {
    return "Good question! The exponent comes down as a factor, so $\\frac{d}{dx}x^3 = 3x^2$.";
  }
  if (task === "explain") {
    return (
      `**${concept.trim()}** in one line: the derivative measures how fast a function changes.\n\n` +
      "For powers there is a shortcut, the *power rule*:\n\n$$\\frac{d}{dx}x^n = n\\,x^{n-1}$$\n\n" +
      "For example, the derivative of $x^2$ is $2x$. Does that make sense?"
    );
  }
  return "Let's look at it another way: for $x^2$, bring the 2 down and lower the power by one, giving $2x$.";
}

function questions(prompt) {
  const count = Number((prompt.match(/Write (\d+) different question/) ?? [])[1] ?? 1);
  return {
    questions: Array.from({ length: count }, () => {
      questionCounter += 1;
      return {
        content: `Question ${questionCounter}: which expression is the derivative of $x^2$?`,
        type: "mcq",
        difficulty: 0.4,
        correct_answer: "A",
        explanation: "By the power rule, $\\frac{d}{dx}x^2 = 2x$.",
        options: [
          { label: "A", text: "$2x$", is_correct: true, misconception: null },
          { label: "B", text: "$x$", is_correct: false, misconception: "power_rule_confusion" },
          { label: "C", text: "$2x^2$", is_correct: false, misconception: "kept_the_exponent" },
          { label: "D", text: "$x^3/3$", is_correct: false, misconception: "integrated_instead" },
        ],
        hints: ["Use the power rule.", "Bring the exponent down as a factor."],
        misconceptions_tested: ["power_rule_confusion"],
      };
    }),
  };
}

function evaluate(prompt) {
  const expected = ((prompt.match(/Correct answer: (.+)/) ?? [])[1] ?? "").trim().toLowerCase();
  const given = ((prompt.match(/---USER INPUT---\n([\s\S]*?)\n---END USER INPUT---/) ?? [])[1] ?? "")
    .trim()
    .toLowerCase();
  const correct = Boolean(expected) && (given === expected || expected.includes(given) || given.includes(expected));
  return {
    is_correct: correct,
    score: correct ? 1 : 0,
    explanation: correct ? "Exactly right." : "Not quite — remember to use the power rule.",
    misconceptions_detected: [],
    follow_up_suggestion: correct ? "advance" : "re_explain",
  };
}

const SESSION_JSON = {
  "generate questions": questions,
  "evaluate an answer": evaluate,
  "diagnose a misconception": () => ({
    misconceptions: [
      {
        name: "power_rule_confusion",
        description: "Forgets to bring the exponent down",
        confidence: 0.8,
        evidence: "Dropped the factor",
      },
    ],
  }),
};

const SUMMARY_TEXT =
  "You put in solid work on derivatives today and practised the power rule. " +
  "Next time, focus on bringing the exponent down every time. Come back tomorrow for a quick review!";

/** OpenAI streaming format: one `data:` frame per word, then usage and [DONE]. */
async function streamText(res, model, text, promptTokens) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
  const frame = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  for (const piece of text.match(/\S+\s*/g) ?? [text]) {
    frame({ id: "chatcmpl-stream", object: "chat.completion.chunk", model, choices: [{ index: 0, delta: { content: piece } }] });
    await new Promise((r) => setTimeout(r, 15));
  }
  frame({ id: "chatcmpl-stream", object: "chat.completion.chunk", model, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
  frame({ id: "chatcmpl-stream", object: "chat.completion.chunk", model, choices: [], usage: { prompt_tokens: promptTokens, completion_tokens: tokens(text) } });
  res.end("data: [DONE]\n\n");
}

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

createServer((req, res) => {
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true });
    let body = {};
    try {
      body = raw ? JSON.parse(raw) : {};
    } catch {
      return send(res, 400, { error: { message: "invalid JSON" } });
    }
    if (req.method === "POST" && url.pathname === "/v1/chat/completions") {
      const prompt = (body.messages ?? []).map((m) => m.content).join("\n");
      const last = body.messages?.at(-1)?.content ?? "";
      const task = (last.match(/## Task: ([^\n]+)/) ?? [])[1]?.trim() ?? "";
      if (body.stream) return void streamText(res, body.model, tutorText(task, last), tokens(prompt));
      const answer = last.includes("NEW concepts just extracted")
        ? relate(last)
        : last.includes("---CHUNK")
          ? extract(last)
          : SESSION_JSON[task]
            ? SESSION_JSON[task](last)
            : { message: "ok" };
      const content = task === "summarise the session" ? SUMMARY_TEXT : JSON.stringify(answer);
      return send(res, 200, {
        id: `chatcmpl-${Date.now()}`,
        object: "chat.completion",
        model: body.model,
        choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
        usage: { prompt_tokens: tokens(prompt), completion_tokens: tokens(content) },
      });
    }
    if (req.method === "POST" && url.pathname === "/v1/embeddings") {
      const inputs = Array.isArray(body.input) ? body.input : [body.input ?? ""];
      const dims = Number(body.dimensions ?? 1536);
      return send(res, 200, {
        object: "list",
        model: body.model,
        data: inputs.map((t, index) => ({ object: "embedding", index, embedding: embed(String(t), dims) })),
        usage: { prompt_tokens: inputs.reduce((a, t) => a + tokens(String(t)), 0) },
      });
    }
    send(res, 404, { error: { message: "not found" } });
  });
}).listen(PORT, "127.0.0.1", () => console.log(`mock LLM on http://127.0.0.1:${PORT}`));
