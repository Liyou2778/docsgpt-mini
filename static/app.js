"use strict";

/* ------------------------------------------------------------------ state */
const state = {
  docs: [],
  citations: [],
  question: "",
  busy: false,
  pollTimer: null,
};

const $ = (id) => document.getElementById(id);

const STATUS_TEXT = {
  pending: "等待处理",
  processing: "处理中",
  ready: "可检索",
  failed: "失败",
};

/* ------------------------------------------------------------------ utils */
function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function api(path, options) {
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }
  if (!response.ok) {
    throw new Error((data && data.detail) || "HTTP " + response.status);
  }
  return data;
}

/* Highlight query bigrams / latin words inside a source chunk. */
function questionTerms(question) {
  const terms = new Set();
  const latin = question.match(/[a-zA-Z0-9_]+/g) || [];
  latin.forEach((token) => {
    if (token.length >= 2) terms.add(token.toLowerCase());
  });
  const runs = question.match(/[\u4e00-\u9fff]+/g) || [];
  runs.forEach((run) => {
    if (run.length === 1) terms.add(run);
    for (let i = 0; i < run.length - 1; i += 1) {
      terms.add(run.slice(i, i + 2));
    }
  });
  return Array.from(terms).sort((a, b) => b.length - a.length);
}

function highlightTerms(text, question) {
  const escaped = escapeHtml(text);
  const terms = questionTerms(question);
  if (!terms.length) return escaped;
  const pattern = terms.map((term) => escapeRegExp(escapeHtml(term))).join("|");
  try {
    return escaped.replace(new RegExp(pattern, "gi"), (match) => "<mark>" + match + "</mark>");
  } catch (_) {
    return escaped;
  }
}

/* -------------------------------------------------------------- documents */
async function loadDocs() {
  try {
    const data = await api("/api/docs");
    state.docs = data.documents || [];
    renderDocs();
    schedulePoll();
  } catch (error) {
    setHint("加载文档失败：" + error.message, "err");
  }
}

function schedulePoll() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  const running = state.docs.some(
    (doc) => doc.status === "pending" || doc.status === "processing"
  );
  if (running) {
    state.pollTimer = setTimeout(loadDocs, 1200);
  }
}

function renderDocs() {
  const list = $("docList");
  list.innerHTML = "";
  $("docCount").textContent = String(state.docs.length);
  $("docEmpty").classList.toggle("hidden", state.docs.length > 0);

  state.docs.forEach((doc) => {
    const item = document.createElement("li");
    item.className = "doc-item";
    const status = STATUS_TEXT[doc.status] || doc.status;
    let extra = "";
    if (doc.error) {
      extra = '<div class="doc-meta" style="color:var(--err)">' + escapeHtml(doc.error) + "</div>";
    }
    item.innerHTML =
      '<div class="doc-head">' +
      '<div class="doc-title">' + escapeHtml(doc.title) + "</div>" +
      '<button class="doc-del" title="删除文档">×</button>' +
      "</div>" +
      '<div class="doc-meta">' +
      '<span class="badge ' + escapeHtml(doc.status) + '">' + escapeHtml(status) + "</span>" +
      "<span>" + Number(doc.chunk_count || 0) + " 块</span>" +
      "<span>" + Number(doc.char_count || 0) + " 字</span>" +
      "</div>" + extra;
    item.querySelector(".doc-del").addEventListener("click", () => deleteDoc(doc));
    list.appendChild(item);
  });
}

function setHint(message, kind) {
  const hint = $("ingestHint");
  hint.textContent = message || "";
  hint.className = "ingest-hint" + (kind ? " " + kind : "");
}

async function ingest(text, title, filename) {
  if (!text || !text.trim()) {
    setHint("内容为空，无法入库。", "err");
    return;
  }
  const payload = { text: text };
  if (title) payload.title = title;
  if (filename) payload.filename = filename;
  setHint("提交中…");
  try {
    const data = await api("/api/docs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (data.created) {
      setHint("已入队，状态 pending → processing → ready。", "ok");
    } else {
      setHint("内容重复，返回已有文档（幂等，未重复入库）。", "ok");
    }
    $("titleInput").value = "";
    $("pasteInput").value = "";
    await loadDocs();
    loadHealth();
  } catch (error) {
    setHint("入库失败：" + error.message, "err");
  }
}

async function deleteDoc(doc) {
  if (!window.confirm("删除文档《" + doc.title + "》及其分块？")) return;
  try {
    await api("/api/docs/" + encodeURIComponent(doc.id), { method: "DELETE" });
    setHint("已删除《" + doc.title + "》。", "ok");
    await loadDocs();
    loadHealth();
  } catch (error) {
    setHint("删除失败：" + error.message, "err");
  }
}

/* ------------------------------------------------------------------ health */
async function loadHealth() {
  try {
    const health = await api("/api/health");
    const documents = health.documents || {};
    const ready = Number(documents.ready || 0);
    const total = Object.values(documents).reduce((sum, value) => sum + Number(value || 0), 0);
    const llm = (health.llm && health.llm.provider) || "mock";
    $("health").innerHTML =
      "LLM <b>" + escapeHtml(llm) + "</b> · 文档 <b>" + total +
      "</b>（ready " + ready + "） · 分块 <b>" +
      Number((health.index && health.index.chunks) || 0) + "</b>";
  } catch (_) {
    $("health").textContent = "服务未连接";
  }
}

/* -------------------------------------------------------------------- chat */
function addMessage(role, html, isError) {
  const log = $("chatLog");
  const node = document.createElement("div");
  node.className = "msg " + role + (isError ? " error" : "");
  node.innerHTML = html;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

function renderAnswer(data) {
  const safe = escapeHtml(data.answer || "");
  const withRefs = safe.replace(/\[(\d{1,3})\]/g, (match, num) => {
    return '<button class="cite-ref" data-cite="' + num + '">[' + num + "]</button>";
  });
  const retrieval = data.retrieval || {};
  const meta =
    '<div class="msg-meta">strategy=' + escapeHtml(retrieval.strategy || "-") +
    " · alpha=" + escapeHtml(String(retrieval.alpha)) +
    " · 检索 " + Number(retrieval.latency_ms || 0).toFixed(1) + " ms" +
    " · 端到端 " + Number(data.latency_ms || 0).toFixed(1) + " ms" +
    " · 引用 " + (data.citations || []).length + " 条</div>";
  addMessage("assistant", "<div>" + withRefs + "</div>" + meta, false);
}

async function ask(question) {
  question = (question || $("questionInput").value || "").trim();
  if (!question || state.busy) return;
  state.busy = true;
  state.question = question;
  $("askBtn").disabled = true;
  $("questionInput").value = "";
  addMessage("user", escapeHtml(question), false);
  const thinking = addMessage("assistant", "检索并生成回答中…", false);
  try {
    const strategy = $("strategySelect").value;
    const payload = {
      question: question,
      strategy: strategy,
      top_k: Number($("topKSelect").value),
    };
    if (strategy === "weighted") {
      payload.alpha = Number($("alphaRange").value);
    }
    const data = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    thinking.remove();
    renderAnswer(data);
    state.citations = data.citations || [];
    renderSources(data.citations || [], question);
    renderDiagnostics(data);
    loadHealth();
  } catch (error) {
    thinking.remove();
    addMessage(
      "assistant",
      "<div>请求失败：" + escapeHtml(error.message) + "</div>",
      true
    );
  } finally {
    state.busy = false;
    $("askBtn").disabled = false;
  }
}

/* ---------------------------------------------------------------- sources */
function renderSources(citations, question) {
  const list = $("sourceList");
  list.innerHTML = "";
  $("sourceEmpty").classList.toggle("hidden", citations.length > 0);
  $("sourceEmpty").textContent = "本次回答没有可用的引用。";

  citations.forEach((citation) => {
    const card = document.createElement("div");
    card.className = "source-card";
    card.dataset.index = String(citation.index);
    card.innerHTML =
      '<div class="source-head">' +
      '<span class="source-index">' + citation.index + "</span>" +
      '<div><div class="source-title">' + escapeHtml(citation.doc_title) + "</div>" +
      '<div class="source-sub">第 ' + (Number(citation.seq) + 1) + " 块 · 字符 " +
      citation.char_start + "–" + citation.char_end + " · " +
      escapeHtml(citation.chunk_id) + "</div></div>" +
      "</div>" +
      '<div class="source-text">' + highlightTerms(citation.text, question) + "</div>" +
      '<div class="source-scores">' +
      "融合 <b>" + Number(citation.score).toFixed(3) + "</b>" +
      " · BM25 <b>" + Number(citation.bm25_score).toFixed(2) + "</b> (rank " + citation.bm25_rank + ")" +
      " · 向量 <b>" + Number(citation.vector_score).toFixed(3) + "</b> (rank " + citation.vector_rank + ")" +
      "</div>";
    card.addEventListener("click", () => activateCitation(citation.index));
    list.appendChild(card);
  });
}

function activateCitation(index) {
  document.querySelectorAll(".source-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.index === String(index));
  });
  document.querySelectorAll(".cite-ref").forEach((button) => {
    button.classList.toggle("active", button.dataset.cite === String(index));
  });
  const active = document.querySelector('.source-card[data-index="' + index + '"]');
  if (active) {
    active.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function renderDiagnostics(data) {
  const details = $("diagnostics");
  const body = $("diagnosticsBody");
  const retrieval = data.retrieval || {};
  const hits = (retrieval.hits || []).map((hit) => ({
    rank: Number(hit.bm25_rank) + "/" + Number(hit.vector_rank),
    chunk_id: hit.chunk_id,
    doc: hit.doc_title,
    seq: hit.seq,
    score: hit.score,
    bm25: hit.bm25_score,
    vector: hit.vector_score,
  }));
  body.textContent = JSON.stringify(
    {
      strategy: retrieval.strategy,
      alpha: retrieval.alpha,
      top_k: retrieval.top_k,
      total_chunks: retrieval.total_chunks,
      retrieval_latency_ms: retrieval.latency_ms,
      llm: data.llm,
      traceability: data.traceability,
      top_hits: hits,
    },
    null,
    2
  );
  details.hidden = false;
}

/* ------------------------------------------------------------------ wiring */
function bindEvents() {
  $("refreshBtn").addEventListener("click", () => {
    loadDocs();
    loadHealth();
  });

  $("ingestBtn").addEventListener("click", () => {
    ingest($("pasteInput").value, $("titleInput").value.trim(), "");
  });

  $("fileInput").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const title = $("titleInput").value.trim() || file.name.replace(/\.[^.]+$/, "");
      ingest(String(reader.result || ""), title, file.name);
    };
    reader.onerror = () => setHint("读取文件失败。", "err");
    reader.readAsText(file, "utf-8");
    event.target.value = "";
  });

  $("askBtn").addEventListener("click", () => ask());
  $("questionInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask();
    }
  });

  $("samples").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-q]");
    if (button) ask(button.dataset.q);
  });

  $("chatLog").addEventListener("click", (event) => {
    const button = event.target.closest(".cite-ref");
    if (button) activateCitation(button.dataset.cite);
  });

  $("alphaRange").addEventListener("input", () => {
    $("alphaValue").textContent = Number($("alphaRange").value).toFixed(2);
  });

  $("strategySelect").addEventListener("change", () => {
    $("alphaWrap").classList.toggle("hidden", $("strategySelect").value === "rrf");
  });
}

bindEvents();
loadDocs();
loadHealth();
