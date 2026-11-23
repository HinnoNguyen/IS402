// Web demo Trợ lý tín dụng – gọi API backend (/api/*), backend mới giữ key và gọi Azure endpoint.
const $ = (sel) => document.querySelector(sel);

const CLASSIFY_SAMPLES = [
  "I lost my card, please block it",
  "What is my current balance?",
  "Someone used my card without permission",
  "My transfer failed and I want to complain",
  "Can I change my PIN at an ATM?",
];
const CHAT_SAMPLES = [
  "Phí rút tiền mặt bằng thẻ tín dụng là bao nhiêu?",
  "Thẻ Bạch Kim được miễn lãi tối đa bao nhiêu ngày?",
  "Tôi bị mất thẻ, bây giờ phải làm gì?",
  "Khiếu nại giao dịch trong thời hạn bao lâu?",
];

async function api(path, body) {
  const resp = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `Lỗi HTTP ${resp.status}`);
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

function chips(container, samples, onPick) {
  for (const s of samples) {
    const b = el("button", { type: "button", class: "chip" }, s);
    b.addEventListener("click", () => onPick(s));
    container.append(b);
  }
}

// ---------- Phản hồi Thích/Không thích (T39) -> /api/feedback -> Cosmos DB ----------
const uuid = () =>
  crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

// session_id là partition key của container feedback; giữ nguyên trong 1 tab trình duyệt
const SESSION_ID = (() => {
  try {
    let id = sessionStorage.getItem("session_id");
    if (!id) sessionStorage.setItem("session_id", (id = uuid()));
    return id;
  } catch {
    return uuid();
  }
})();

// record: {kind, question, answer, sources?, blocked?, confidence?, model_mode}
function feedbackBar(record, { up = "Hữu ích", down = "Chưa đúng", hint = "Chưa đúng ở đâu? (không bắt buộc)" } = {}) {
  const id = uuid(); // mỗi câu trả lời 1 bản ghi; bấm lại hoặc thêm góp ý thì ghi đè bản ghi đó
  let rating = null;
  const status = el("span", { class: "fb-status", role: "status" });
  const comment = el("input", { type: "text", maxlength: "500", placeholder: hint, "aria-label": hint });
  const send = el("button", { type: "button", class: "ghost" }, "Gửi góp ý");
  const commentRow = el("div", { class: "fb-comment" }, comment, send);
  commentRow.hidden = true;

  async function submit() {
    status.textContent = "Đang gửi…";
    const body = { id, session_id: SESSION_ID, rating, ...record,
                   comment: rating === "down" ? comment.value.trim() : "" };
    try {
      const res = await api("/api/feedback", body);
      status.textContent = res.mode === "live" ? "Đã lưu vào Cosmos DB, cảm ơn bạn!" : "Đã ghi nhận (MOCK)";
      return true;
    } catch (err) {
      status.textContent = `Chưa gửi được: ${err.message}`;
      return false;
    }
  }

  const buttons = [["up", "👍", up], ["down", "👎", down]].map(([value, icon, label]) => {
    const b = el("button", { type: "button", class: `fb-btn ${value}`, title: label, "aria-label": label,
                             "aria-pressed": "false" }, icon);
    b.addEventListener("click", () => {
      rating = value;
      buttons.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      commentRow.hidden = value !== "down";
      if (!commentRow.hidden) commentRow.scrollIntoView({ block: "nearest" });
      submit();
    });
    return b;
  });
  send.addEventListener("click", async () => {
    if (comment.value.trim() && (await submit())) commentRow.hidden = true;
  });
  comment.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); send.click(); }
  });
  return el("div", { class: "feedback" }, el("div", { class: "fb-row" }, ...buttons, status), commentRow);
}

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      const active = t === tab;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", String(active));
      $(`#tab-${t.dataset.tab}`).hidden = !active;
    });
  });
});

// ---------- Trạng thái endpoint ----------
async function loadStatus() {
  const box = $("#status");
  const badge = (name, mode) =>
    el("span", { class: `badge ${mode}` }, `${name}: ${mode === "live" ? "Azure" : mode === "mock" ? "MOCK" : "lỗi"}`);
  try {
    const h = await api("/api/health");
    box.replaceChildren(badge("Phân loại", h.classifier), badge("Chatbot", h.chat), badge("Phản hồi", h.feedback));
  } catch {
    box.replaceChildren(badge("Backend", "down"));
  }
}

// ---------- Phân loại ----------
chips($("#classify-samples"), CLASSIFY_SAMPLES, (s) => {
  const input = $("#classify-input");
  input.value = input.value.trim() ? `${input.value.trim()}\n${s}` : s;
  input.focus();
});

$("#classify-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const texts = $("#classify-input").value.split("\n").map((t) => t.trim()).filter(Boolean);
  if (!texts.length) return;
  const btn = $("#classify-btn");
  btn.disabled = true;
  btn.textContent = "Đang phân loại…";
  const list = $("#predictions");
  try {
    const res = await api("/api/classify", { texts });
    $("#classify-meta").textContent =
      `${res.mode === "live" ? "Azure ML endpoint" : "MOCK"} · ${res.predictions.length} câu · ${res.latency_ms} ms`;
    list.replaceChildren(
      ...res.predictions.map((p) => {
        const pct = Math.round(p.confidence * 100);
        const color = `var(--c-${p.label}, var(--c-other))`;
        return el("li", { class: "prediction", style: `--label-color:${color}` },
          el("span", { class: "text" }, p.text),
          el("span", { class: "label", title: p.label }, p.label_vi),
          el("div", { class: "bar" },
            el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${pct}%` })),
            `Độ tin cậy ${pct}%`),
          feedbackBar(
            { kind: "classify", question: p.text, answer: p.label, confidence: p.confidence, model_mode: res.mode },
            { up: "Nhãn đúng", down: "Nhãn sai", hint: "Nhãn đúng phải là gì? (không bắt buộc)" }));
      }));
  } catch (err) {
    $("#classify-meta").textContent = "";
    list.replaceChildren(el("li", { class: "prediction flag" }, `Không phân loại được: ${err.message}`));
  } finally {
    $("#classify-result").hidden = false;
    btn.disabled = false;
    btn.textContent = "Phân loại";
  }
});

// ---------- Chat ----------
let history = []; // [{question, answer}] – chỉ lưu các lượt không bị chặn

// Đoạn trích lấy từ PDF còn nhiều dòng trống, khoảng trắng thừa -> gộp lại cho gọn rồi cắt bớt
function shorten(text, max) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max).trimEnd() + "…" : flat;
}

function addMessage(role, text, extra = {}) {
  const box = $("#messages");
  const msg = el("div", { class: `msg ${role}${extra.cls ? " " + extra.cls : ""}` },
    el("div", { class: "bubble" }, text));
  if (extra.sources?.length) {
    msg.append(el("details", { class: "sources" },
      el("summary", {}, `Nguồn trích dẫn (${extra.sources.length})`),
      el("ol", {}, ...extra.sources.map((s) =>
        el("li", {}, el("strong", {}, s.title),
          el("span", { class: "excerpt" }, shorten(s.excerpt, 300)))))));
  }
  if (extra.meta) msg.append(el("span", { class: "meta" }, extra.meta));
  if (extra.feedback) msg.append(feedbackBar(extra.feedback));
  box.append(msg);
  box.scrollTop = box.scrollHeight;
  return msg;
}

chips($("#chat-samples"), CHAT_SAMPLES, (s) => {
  $("#chat-input").value = s;
  $("#chat-input").focus();
});

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  const btn = $("#chat-btn");
  btn.disabled = true;
  addMessage("user", question);
  const typing = addMessage("bot", "Đang tra cứu điều khoản…", { cls: "typing" });
  try {
    const res = await api("/api/chat", { question, chat_history: history });
    typing.remove();
    const source = res.mode === "live" ? "Prompt Flow endpoint" : "MOCK";
    const feedback = { kind: "chat", question, answer: res.answer, blocked: res.blocked, model_mode: res.mode,
                       sources: res.sources.slice(0, 10).map((s) => `Nguồn ${s.id}: ${s.title} – ${shorten(s.excerpt, 200)}`) };
    if (res.blocked) {
      addMessage("bot", res.answer, { cls: "blocked", feedback,
        meta: `⚠ Câu hỏi bị bộ lọc an toàn chặn · ${source} · ${res.latency_ms} ms` });
    } else {
      addMessage("bot", res.answer, { sources: res.sources, feedback, meta: `${source} · ${res.latency_ms} ms` });
      history = [...history, { question, answer: res.answer }].slice(-10);
    }
  } catch (err) {
    typing.remove();
    addMessage("bot", `Không gọi được chatbot: ${err.message}`, { cls: "error" });
  } finally {
    btn.disabled = false;
    input.focus();
  }
});

$("#chat-reset").addEventListener("click", () => {
  history = [];
  const box = $("#messages");
  box.replaceChildren(box.firstElementChild);
});

loadStatus();
