const byId = (id) => document.getElementById(id);
const chatThread = byId("chat-thread");
const chatInput = byId("chat-input");
const history = [];
const MAX_VISIBLE_MESSAGES = 40;
let lastSnapshot = null;
let busy = false;
let searchResults = [];

function toast(message, error = false) {
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.textContent = message;
  byId("toast-region").append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function addMessage(role, content = "", isError = false) {
  const row = document.createElement("div");
  row.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "◉" : "✳";
  const bubble = document.createElement("div");
  bubble.className = `bubble${isError ? " error" : ""}`;
  bubble.textContent = content;
  row.append(avatar, bubble);
  chatThread.append(row);
  while (chatThread.querySelectorAll(".message").length > MAX_VISIBLE_MESSAGES) {
    chatThread.querySelector(".message")?.remove();
  }
  chatThread.scrollTop = chatThread.scrollHeight;
  return bubble;
}

function cleanWelcome() {
  chatThread.querySelector(".welcome-card")?.remove();
}

function setBusy(value) {
  busy = value;
  byId("send-button").disabled = value;
  byId("send-button").querySelector("span:first-child").textContent = value ? "応答中" : "送信";
}

async function sendChat(message) {
  if (busy || !message.trim()) return;
  cleanWelcome();
  addMessage("user", message.trim());
  const assistant = addMessage("assistant");
  const webSearch = byId("chat-web-search").checked;
  setBusy(true);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message.trim(), history: history.slice(-12), web_search: webSearch }),
    });
    if (!response.ok || !response.body) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || `チャット要求に失敗しました (${response.status})`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    let answer = "";
    let sources = [];
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const messages = pending.split("\n\n");
      pending = messages.pop() || "";
      for (const raw of messages) {
        const line = raw.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.error) throw new Error(event.error);
        if (event.token) {
          answer += event.token;
          assistant.textContent = answer;
          chatThread.scrollTop = chatThread.scrollHeight;
        }
        if (event.done) sources = event.sources || [];
      }
    }
    if (!answer) answer = "モデルから回答がありませんでした。";
    assistant.textContent = answer;
    if (sources.length) {
      const sourceBox = document.createElement("div");
      sourceBox.className = "sources";
      sourceBox.textContent = "参照したWeb検索結果";
      for (const source of sources) {
        const link = document.createElement("a");
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = source.title;
        sourceBox.append(link);
      }
      assistant.append(sourceBox);
    }
    history.push({ role: "user", content: message.trim() }, { role: "assistant", content: answer });
    history.splice(0, Math.max(0, history.length - 12));
  } catch (error) {
    assistant.textContent = error.message;
    assistant.classList.add("error");
    toast(error.message, true);
  } finally {
    setBusy(false);
    chatInput.focus();
  }
}

byId("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput.value;
  chatInput.value = "";
  sendChat(message);
});
chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    byId("chat-form").requestSubmit();
  }
});
document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    chatInput.value = button.dataset.prompt;
    if (button.dataset.prompt.includes("調べて")) byId("chat-web-search").checked = true;
    chatInput.focus();
  });
});

function renderSearchResults(results) {
  searchResults = results;
  const root = byId("search-results");
  root.replaceChildren();
  if (!results.length) {
    root.innerHTML = '<div class="knowledge-empty">検索結果がありませんでした。検索語を変えてみてください。</div>';
    return;
  }
  results.forEach((result, index) => {
    const card = document.createElement("article");
    card.className = "search-result";
    const link = document.createElement("a");
    link.href = result.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = result.title;
    const snippet = document.createElement("p");
    snippet.textContent = result.snippet;
    const actions = document.createElement("div");
    actions.className = "result-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "＋ 工場の知識に保存";
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const response = await fetch("/api/knowledge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ result }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "知識を保存できませんでした");
        save.textContent = data.added ? "✓ 保存しました" : "✓ 保存済み";
        save.classList.add("saved");
        toast(data.added ? "知識を保存しました。次の開発サイクルから参考にします。" : "この知識は既に保存されています。");
        refreshStatus();
      } catch (error) {
        save.disabled = false;
        toast(error.message, true);
      }
    });
    const ask = document.createElement("button");
    ask.type = "button";
    ask.textContent = "会話に使う";
    ask.addEventListener("click", () => {
      byId("chat-web-search").checked = true;
      chatInput.value = `「${result.title}」について、検索情報を踏まえて説明してください。`;
      chatInput.focus();
    });
    actions.append(save, ask);
    card.append(link, snippet, actions);
    root.append(card);
    if (index >= 4) return;
  });
}

byId("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = byId("search-input").value.trim();
  if (!query) return;
  const button = byId("search-button");
  button.disabled = true;
  button.textContent = "…";
  byId("search-results").innerHTML = '<div class="knowledge-empty">Webを検索しています…</div>';
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Web検索に失敗しました");
    renderSearchResults(data.results || []);
  } catch (error) {
    byId("search-results").innerHTML = "";
    const message = document.createElement("div");
    message.className = "knowledge-empty";
    message.textContent = error.message;
    byId("search-results").append(message);
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "⌕";
  }
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: button.dataset.action }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "工場を操作できませんでした");
      toast(`${button.textContent.trim()} を受け付けました`);
      refreshStatus();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
});

function formatTime(value) {
  if (!value) return "時刻不明";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "時刻不明" : date.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
}

function renderTimeline(snapshot) {
  const root = byId("timeline");
  const runs = snapshot.recent_runs || [];
  const events = snapshot.recent_events || [];
  root.replaceChildren();
  if (!runs.length && !events.length) {
    const empty = document.createElement("div");
    empty.className = "timeline-empty";
    empty.textContent = snapshot.ollama?.available
      ? "最初の実験を待っています。工場が稼働するとここに表示されます。"
      : "Ollamaの起動後、実験の進捗がここに表示されます。";
    root.append(empty);
    return;
  }
  events.forEach((event) => {
    const item = document.createElement("div");
    item.className = "timeline-item";
    const rail = document.createElement("div");
    rail.className = "timeline-rail";
    const marker = document.createElement("span");
    marker.className = "timeline-marker rejected";
    rail.append(marker);
    const copy = document.createElement("div");
    copy.className = "timeline-copy";
    const title = document.createElement("strong");
    title.textContent = event.message || "自動回復イベント";
    const detail = document.createElement("small");
    detail.textContent = `${formatTime(event.created_at)} · ${event.model} · ${event.details?.retry_in_seconds ?? 0}秒後に再試行`;
    copy.append(title, detail);
    const badge = document.createElement("span");
    badge.className = "timeline-badge rejected";
    badge.textContent = "RETRY";
    item.append(rail, copy, badge);
    root.append(item);
  });
  runs.forEach((run) => {
    const task = (snapshot.tasks || []).find((item) => item.id === run.task_id);
    const item = document.createElement("div");
    item.className = "timeline-item";
    const rail = document.createElement("div");
    rail.className = "timeline-rail";
    const marker = document.createElement("span");
    marker.className = `timeline-marker ${run.status}`;
    rail.append(marker);
    const copy = document.createElement("div");
    copy.className = "timeline-copy";
    const title = document.createElement("strong");
    title.textContent = task?.title || `実験 ${run.id.slice(0, 8)}`;
    const detail = document.createElement("small");
    detail.textContent = `${formatTime(run.started_at)} · ${Number(run.duration_seconds || 0).toFixed(1)}秒`;
    copy.append(title, detail);
    const badge = document.createElement("span");
    badge.className = `timeline-badge ${run.status}`;
    badge.textContent = run.status === "approved" ? "PASS" : "REVIEW";
    item.append(rail, copy, badge);
    root.append(item);
  });
}

function renderSnapshot(snapshot) {
  lastSnapshot = snapshot;
  const state = snapshot.status || {};
  const status = state.status || "stopped";
  const projectReady = snapshot.project?.configured !== false;
  const setupNotice = byId("setup-notice");
  setupNotice.hidden = projectReady;
  if (!projectReady) {
    byId("setup-config-path").textContent = snapshot.project?.config_path || "config.json";
  }
  byId("factory-status").textContent = ({
    running: "稼働中", idle: "待機中", paused: "一時停止", stopping: "停止処理中", stopped: "停止",
  })[status] || status;
  const dot = byId("status-dot");
  dot.className = `pulse-dot${status === "running" || status === "idle" ? " running" : status === "stopped" ? " stopped" : ""}`;
  byId("model-name").textContent = snapshot.ollama?.model || "--";
  byId("model-health").textContent = snapshot.ollama?.available
    ? (snapshot.ollama.model_installed ? "接続済み" : "モデル未取得")
    : "未接続";
  byId("model-detail").textContent = snapshot.ollama?.available
    ? (snapshot.ollama.model_installed ? "Ollama · ローカル推論" : "ollama pull でモデルを取得")
    : "Ollamaを起動してください";
  byId("approved-count").textContent = snapshot.report?.approved ?? 0;
  byId("success-rate").textContent = snapshot.report?.success_rate == null
    ? "—" : `${Math.round(snapshot.report.success_rate * 100)}%`;
  byId("run-count").textContent = `${snapshot.report?.total ?? 0} 回の評価`;
  byId("knowledge-count").textContent = snapshot.knowledge?.count ?? 0;
  const task = state.current_task;
  const taskNode = byId("current-task");
  taskNode.querySelector("strong").textContent = ["自動回復待ち", "再試行待ち"].includes(state.reason)
    ? `${state.reason} · ${state.retry_in_seconds ?? "?"}秒後に再試行`
    : task || (
      status === "paused" ? (state.reason || "一時停止しています")
        : !projectReady ? "開発対象が見つかりません"
        : status === "stopped" ? "工場は停止中です" : "タスクを待機しています"
    );
  taskNode.querySelector(".task-indicator").className = `task-indicator${status === "running" ? " running" : ""}`;
  if (status === "running" && state.phase) {
    const phase = ({ analyzing: "プロジェクトを分析中", generating: "モデルがコードを生成中", evaluating: "テストと品質を評価中", saving: "実験結果を記録中" })[state.phase] || state.phase;
    taskNode.querySelector("strong").textContent = `${phase} · ${task || "タスク実行中"}`;
  }
  renderTimeline(snapshot);
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`状態を取得できません (${response.status})`);
    renderSnapshot(await response.json());
  } catch (error) {
    byId("factory-status").textContent = "接続エラー";
    toast(error.message, true);
  }
}

const eventSource = new EventSource("/api/events");
eventSource.onmessage = (event) => {
  try { renderSnapshot(JSON.parse(event.data)); } catch { /* Ignore malformed status events. */ }
};
eventSource.onerror = () => {
  if (lastSnapshot === null) refreshStatus();
};

setInterval(() => {
  byId("clock").textContent = new Date().toLocaleTimeString("ja-JP", { hour12: false });
}, 1000);
refreshStatus();
