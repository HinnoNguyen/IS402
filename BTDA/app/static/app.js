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
    box.replaceChildren(badge("Phân loại", h.classifier), badge("Chatbot", h.chat));
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
            `Độ tin cậy ${pct}%`));
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

function addMessage(role, text, extra = {}) {
  const box = $("#messages");
  const msg = el("div", { class: `msg ${role}${extra.cls ? " " + extra.cls : ""}` },
    el("div", { class: "bubble" }, text));
  if (extra.sources?.length) {
    msg.append(el("details", { class: "sources" },
      el("summary", {}, `Nguồn trích dẫn (${extra.sources.length})`),
      el("ol", {}, ...extra.sources.map((s) =>
        el("li", {}, el("strong", {}, s.title),
          el("span", { class: "excerpt" }, s.excerpt.length > 400 ? s.excerpt.slice(0, 400) + "…" : s.excerpt))))));
  }
  if (extra.meta) msg.append(el("span", { class: "meta" }, extra.meta));
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
    if (res.blocked) {
      addMessage("bot", res.answer, { cls: "blocked", meta: `⚠ Câu hỏi bị bộ lọc an toàn chặn · ${source} · ${res.latency_ms} ms` });
    } else {
      addMessage("bot", res.answer, { sources: res.sources, meta: `${source} · ${res.latency_ms} ms` });
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
