/**
 * خادم Mastra جانبي (Node) - يكمّل خط أنابيب بايثون ولا يكرّره.
 *
 * لماذا موجود أصلاً؟ Mastra إطار TypeScript فقط بلا SDK لبايثون، فالطريق
 * الوحيد لضمّه هو تشغيله كخادم مستقل يناديه بايثون عبر HTTP.
 *
 * وحتى يستحق وجوده، لا يكرّر ما يفعله CrewAI (بحث، أفكار، نقد). دوره
 * واحد: **تقييم مستقل** للتقرير النهائي بنموذج ثانٍ - حَكَم خارج الطاقم
 * الذي أنتج العمل. الطاقم لا يستطيع الحكم على نفسه بحياد.
 *
 * التشغيل:  node server.mjs          (المنفذ MASTRA_PORT أو 8740)
 */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { Agent } from "@mastra/core/agent";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

const PORT = Number(process.env.MASTRA_PORT || 8740);

// نقرأ .env من مجلد المشروع الأب - مصدر واحد للمفاتيح لا نسختان
function loadEnv() {
  try {
    const txt = readFileSync(new URL("../.env", import.meta.url), "utf8");
    for (const line of txt.split(/\r?\n/)) {
      const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)$/.exec(line);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
    }
  } catch {
    /* لا ملف .env - نعتمد على بيئة العملية */
  }
}
loadEnv();

const MODEL = (process.env.MODEL || "openrouter/minimax/minimax-m3:free")
  .replace(/^openrouter\//, "");

const provider = createOpenAICompatible({
  name: "openrouter",
  baseURL: "https://openrouter.ai/api/v1",
  apiKey: process.env.OPENROUTER_API_KEY,
});

const judge = new Agent({
  name: "محكّم التقارير",
  instructions: `أنت محكّم مستقل. لم تشارك في كتابة التقرير، ومهمتك تقييمه بصرامة.

قيّم على خمسة محاور، كل منها من 10:
1. الاستناد للمصادر - هل الأرقام منسوبة أم مخترعة؟
2. الجدّة - هل الأفكار مألوفة أم فيها زاوية غير متوقعة؟
3. القابلية للتنفيذ - هل الخطوات ملموسة أم شعارات؟
4. الاتساق الداخلي - هل تتناقض أجزاء التقرير؟
5. الواقعية الرقمية - هل التقديرات معقولة في مجالها؟

أعد JSON فقط بلا أي نص خارجه:
{"scores":{"sourcing":n,"novelty":n,"actionability":n,"consistency":n,"realism":n},
 "total":n,"verdict":"...","weakest":"...","fix":"..."}`,
  model: provider(MODEL),
});

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

function send(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    return send(res, 200, { ok: true, model: MODEL, role: "judge" });
  }

  if (req.method === "POST" && req.url === "/judge") {
    try {
      const { report = "", topic = "" } = JSON.parse(await readBody(req) || "{}");
      if (!report.trim()) return send(res, 400, { error: "لا يوجد تقرير" });

      // نقتطع المدخل: التقارير تتجاوز 40 ألف حرف والحكم لا يحتاجها كاملة
      const excerpt = report.slice(0, 18000);
      const out = await judge.generate(
        `الموضوع: ${topic}\n\n--- التقرير ---\n${excerpt}`
      );

      const text = out.text ?? String(out);
      const m = text.match(/\{[\s\S]*\}/);
      if (!m) return send(res, 200, { raw: text, parsed: false });
      return send(res, 200, { ...JSON.parse(m[0]), parsed: true });
    } catch (e) {
      return send(res, 500, { error: `${e.name}: ${e.message}` });
    }
  }

  send(res, 404, { error: "غير موجود" });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Mastra judge on http://127.0.0.1:${PORT}  (model: ${MODEL})`);
});
