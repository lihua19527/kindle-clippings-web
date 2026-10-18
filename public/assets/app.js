// 剪藏台 · 前端逻辑（ES module，无构建）
// 后端只返回数据，所有呈现都在这里做。

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  data: null,        // /api/parse 的响应
  token: null,
  selected: null,    // 当前打开的书 id
  query: "",
  busy: false,
  files: [],         // 已读入内存的剪藏文件
  picked: new Set(), // 勾选待导出的书 id
  packaging: "merged", // merged = 合并成一个文档；per_book = 每本单独打包
};

const PARAMS = new URLSearchParams(location.search);
// ?still=1 关掉动画与平滑滚动，截图和测试用
const REDUCED =
  PARAMS.get("still") === "1" ||
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;
// 注意：scrollIntoView 的 "auto" 会沿用 CSS 的 scroll-behavior: smooth，
// 想真正瞬间到位必须写 "instant"。
const SCROLL = REDUCED ? "instant" : "smooth";
// ?scroll=0 解析完不自动跳到书库（截图要页面停在顶部）
const AUTOSCROLL = PARAMS.get("scroll") !== "0";
if (REDUCED) document.documentElement.classList.add("is-still");

function scrollToSection(el) {
  if (el) el.scrollIntoView({ behavior: SCROLL, block: "start" });
}

/* ------------------------------------------------------------------ 工具 */

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    body = null;
  }
  if (!res.ok || (body && body.ok === false)) {
    const message = (body && (body.error || body.detail)) || `请求失败（HTTP ${res.status}）`;
    throw new Error(message);
  }
  return body;
}

function setStatus(text, kind = "") {
  const el = $("#parse-status");
  el.textContent = text || "";
  el.classList.remove("is-ok", "is-error");
  if (kind) el.classList.add(kind);
}

function decodeBuffer(buf) {
  for (const enc of ["utf-8", "utf-16le", "gb18030"]) {
    try {
      return new TextDecoder(enc, { fatal: true }).decode(buf);
    } catch (err) {
      /* 换下一个编码 */
    }
  }
  return new TextDecoder("utf-8").decode(buf);
}

function bytesLabel(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function clip(text, max = 92) {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

function locationText(entry) {
  // 与导出的 Markdown / PDF / Word 保持一致：直接用 Kindle 的原始位置写法
  return entry.locationRaw || (entry.location ? String(entry.location) : "");
}

function escapeHTML(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const ACCENTS = ["blue", "red", "yellow"];

function kindCounts(book) {
  const c = book.counts || {};
  return {
    note: c["笔记"] || 0,
    bookmark: c["书签"] || 0,
    highlight: c["标注"] || 0,
  };
}

/* ------------------------------------------------------------------ 统计 */

function animateNumber(el, to) {
  if (!Number.isFinite(to)) {
    el.textContent = "—";
    return;
  }
  if (REDUCED) {
    el.textContent = to.toLocaleString("zh-CN");
    return;
  }
  const duration = 460;
  const start = performance.now();
  const final = to.toLocaleString("zh-CN");
  // 先落最终值：rAF 被节流（后台标签页 / 无头截图）时也不会停在中间数
  el.textContent = final;
  const tick = (now) => {
    const p = Math.min(1, (now - start) / duration);
    if (p >= 1) {
      el.textContent = final;
      return;
    }
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(to * eased).toLocaleString("zh-CN");
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderStats() {
  const stats = state.data ? state.data.stats : null;
  animateNumber($("#stat-books"), stats ? stats.books : NaN);
  animateNumber($("#stat-entries"), stats ? stats.entries : NaN);
  animateNumber($("#stat-dropped"), stats ? stats.dropped : NaN);
}

/* ------------------------------------------------------------------ 书库 */

function cardHTML(book, index) {
  const accent = ACCENTS[index % 3];
  const counts = kindCounts(book);
  const spineParts = [];
  if (counts.highlight) spineParts.push(`标注 ${counts.highlight}`);
  if (counts.note) spineParts.push(`笔记 ${counts.note}`);
  if (counts.bookmark) spineParts.push(`书签 ${counts.bookmark}`);

  const ticks = (book.entries || [])
    .slice(0, 45)
    .map((e) => {
      const cls = e.kind === "笔记" ? "is-note" : e.kind === "书签" ? "is-bookmark" : "";
      return `<i${cls ? ` class="${cls}"` : ""}></i>`;
    })
    .join("");

  return `
    <article class="book-card" data-accent="${accent}" data-id="${book.id}" tabindex="0" role="button"
             aria-label="打开《${escapeHTML(book.title)}》的 ${book.kept} 条剪藏">
      <div class="card-spine">
        <span class="spine-no">No. ${String(index + 1).padStart(2, "0")}</span>
        <span>${spineParts.join(" · ") || "剪藏"}</span>
        <label class="card-pick" title="勾选这本书一起导出">
          <input type="checkbox" data-pick="${book.id}"
                 ${state.picked.has(book.id) ? "checked" : ""}
                 aria-label="选择《${escapeHTML(book.title)}》">
        </label>
      </div>
      <div class="card-body">
        <p class="card-overline">${escapeHTML(book.author || "未知作者")}</p>
        <h3 class="card-title">${escapeHTML(book.title)}</h3>
        <p class="card-desc">${escapeHTML(clip(book.firstContent, 96))}</p>
        <div class="card-ticks" aria-hidden="true">${ticks}</div>
        <div class="card-metrics">
          <span><strong>${book.kept}</strong>条剪藏</span>
          <span><strong>${book.dropped}</strong>条重复已去</span>
        </div>
      </div>
      <div class="card-foot">
        <span>打开全书</span>
        <span class="card-more">${escapeHTML(book.span ? `位置 ${book.span}` : `${book.raw} 条原始`)}</span>
      </div>
    </article>`;
}

function filteredBooks() {
  if (!state.data) return [];
  const q = state.query.trim().toLowerCase();
  if (!q) return state.data.books;
  return state.data.books.filter((book) => {
    if (book.title.toLowerCase().includes(q)) return true;
    if ((book.author || "").toLowerCase().includes(q)) return true;
    return (book.entries || []).some((e) => (e.content || "").toLowerCase().includes(q));
  });
}

function renderLibrary() {
  const grid = $("#book-grid");
  const idle = $("#idle-state");
  const empty = $("#empty-state");
  const exportBar = $("#export-bar");
  const status = $("#library-status");

  if (!state.data) {
    grid.innerHTML = "";
    grid.hidden = true;
    idle.hidden = false;
    empty.hidden = true;
    exportBar.hidden = true;
    status.textContent = "";
    return;
  }

  grid.hidden = false;
  idle.hidden = true;
  exportBar.hidden = false;

  const books = filteredBooks();
  grid.innerHTML = books.map((book, i) => cardHTML(book, i)).join("");
  empty.hidden = books.length > 0;
  if (!books.length) grid.hidden = true;

  const total = state.data.stats;
  if (state.query.trim()) {
    status.textContent = `筛选出 ${books.length} 本 / 共 ${total.books} 本`;
  } else {
    status.textContent = `共 ${total.books} 本 · ${total.entries} 条`;
  }
  syncPickUI();
  syncNav();
}

/* ------------------------------------------------------- 多选与导出范围 */

function syncPickUI() {
  const total = state.data ? state.data.books.length : 0;
  const picked = state.picked.size;
  const count = $("#export-count");
  if (count) {
    count.textContent = picked
      ? `已选 ${picked} 本 · 只导出这 ${picked} 本`
      : `全部 ${total} 本 · 合并或分册导出`;
    count.classList.toggle("is-picked", picked > 0);
  }
  const all = $("#pick-all");
  if (all) {
    all.checked = picked > 0 && picked === total;
    all.indeterminate = picked > 0 && picked < total;
  }
}

function togglePick(id, on) {
  if (on) state.picked.add(id);
  else state.picked.delete(id);
  syncPickUI();
}

function pickAllBooks(on) {
  state.picked.clear();
  if (on && state.data) {
    for (const book of filteredBooks()) state.picked.add(book.id);
  }
  renderLibrary();
}

function setPackaging(mode) {
  state.packaging = mode === "per_book" ? "per_book" : "merged";
  $$("#export-bar [data-packaging]").forEach((btn) => {
    const on = btn.dataset.packaging === state.packaging;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  syncPickUI();
}

function entryHTML(entry, index) {
  const isNote = entry.kind === "笔记";
  const isBookmark = entry.kind === "书签";
  const tagClass = isNote ? "is-note" : isBookmark ? "is-bookmark" : "";
  const quoteClass = isNote ? " is-note" : isBookmark ? " is-bookmark" : "";
  const meta = [];
  if (entry.page) meta.push(`第 ${entry.page} 页`);
  const loc = locationText(entry);
  if (loc) meta.push(`位置 ${loc}`);
  if (entry.added) meta.push(entry.added);

  return `
    <li class="entry">
      <div class="entry-head">
        <span class="entry-no">${String(index + 1).padStart(2, "0")}</span>
        <span class="kind-tag ${tagClass}">${escapeHTML(entry.kind || "剪藏")}</span>
      </div>
      <blockquote class="quote${quoteClass}">${escapeHTML(entry.content || "（没有正文）")}</blockquote>
      ${meta.length ? `<p class="entry-meta">${escapeHTML(meta.join(" · "))}</p>` : ""}
    </li>`;
}

function mergedHTML(book) {
  const groups = (book.merged || []).filter((g) => g.dropped && g.dropped.length);
  if (!groups.length) return "";
  const items = groups
    .map((group) => {
      const names = group.dropped
        .map((d) => `${escapeHTML(d.kind || "记录")}「${escapeHTML(clip(d.content, 34))}」`)
        .join("、");
      return `<li><b>第 ${(group.keptAt ?? 0) + 1} 条</b> 吃掉 ${group.dropped.length} 条：${names}</li>`;
    })
    .join("");
  return `
    <details class="merged-note">
      <summary>这本书合并掉了 ${book.dropped} 条重复记录</summary>
      <ul class="merged-list">${items}</ul>
    </details>`;
}

function openBook(id) {
  if (!state.data) return;
  const book = state.data.books.find((b) => b.id === id);
  if (!book) return;
  state.selected = id;

  const counts = kindCounts(book);
  const kindBits = [];
  if (counts.highlight) kindBits.push(`标注 ${counts.highlight}`);
  if (counts.note) kindBits.push(`笔记 ${counts.note}`);
  if (counts.bookmark) kindBits.push(`书签 ${counts.bookmark}`);

  const detail = $("#book-detail");
  detail.dataset.accent = ACCENTS[state.data.books.indexOf(book) % 3];
  detail.innerHTML = `
    <div class="detail-head">
      <div class="detail-bar">
        <button class="btn btn-quiet" type="button" id="detail-back">← 回到书库</button>
        <span class="detail-code">Book ${String(book.id + 1).padStart(2, "0")} / ${state.data.books.length}</span>
      </div>
      <h2 id="detail-title">《${escapeHTML(book.title)}》</h2>
      <p class="detail-meta">${escapeHTML(book.author || "未知作者")} · ${book.kept} 条${kindBits.length ? `（${kindBits.join(" / ")}）` : ""}${book.dropped ? ` · 已合并 ${book.dropped} 条` : ""}</p>
      <div class="detail-actions">
        <span class="detail-actions-label">导出这本</span>
        <button class="btn btn-secondary" type="button" data-scope="book" data-format="md">Markdown</button>
        <button class="btn btn-secondary" type="button" data-scope="book" data-format="pdf">PDF</button>
        <button class="btn btn-secondary" type="button" data-scope="book" data-format="docx">Word</button>
      </div>
    </div>
    <ol class="entry-list">${(book.entries || []).map(entryHTML).join("")}</ol>
    ${mergedHTML(book)}`;

  detail.hidden = false;
  $("#book-grid").hidden = true;
  $("#export-bar").hidden = true;
  $("#library-status").textContent = `《${book.title}》· ${book.kept} 条`;
  if (AUTOSCROLL) scrollToSection(detail);
  syncNav();
}

function closeBook() {
  state.selected = null;
  $("#book-detail").hidden = true;
  $("#book-detail").innerHTML = "";
  renderLibrary();
  scrollToSection($("#library"));
}

/* ------------------------------------------------------------------ 导入 */

function chipList(names) {
  const list = $("#file-chips");
  if (!names.length) {
    list.innerHTML = "";
    return;
  }
  const shown = names.slice(0, 4);
  const rest = names.length - shown.length;
  list.innerHTML =
    shown.map((n) => `<li title="${escapeHTML(n)}">${escapeHTML(clip(n, 46))}</li>`).join("") +
    (rest > 0 ? `<li>+${rest}</li>` : "");
}

async function addFiles(fileList) {
  const all = Array.from(fileList || []);
  const txt = all.filter((f) => /\.txt$/i.test(f.name) || /clippings/i.test(f.name));
  const picked = txt.length ? txt : all;
  if (!picked.length) {
    setStatus("这些文件里没有 .txt，请选择 My Clippings.txt 或包含它的文件夹。", "is-error");
    return;
  }

  setStatus(`正在读取 ${picked.length} 个文件…`);
  const loaded = [];
  for (const file of picked) {
    try {
      const buf = await file.arrayBuffer();
      const text = decodeBuffer(buf);
      if (text.trim()) loaded.push({ name: file.name, content: text, size: buf.byteLength });
    } catch (err) {
      setStatus(`读取 ${file.name} 失败：${err.message}`, "is-error");
      return;
    }
  }

  state.files = loaded;
  chipList(loaded.map((f) => f.name));
  if (!loaded.length) {
    setStatus("这些文件是空的，没有可解析的内容。", "is-error");
    return;
  }
  const bytes = loaded.reduce((sum, f) => sum + f.size, 0);
  setStatus(`已读入 ${loaded.length} 个文件（${bytesLabel(bytes)}）。点「解析剪藏」开始。`, "is-ok");
}

function readOptions() {
  const keepAll = $("#opt-keep-all").checked;
  return {
    keep_all: keepAll,
    fuzzy: !keepAll && $("#opt-fuzzy").checked,
    containment: !keepAll && $("#opt-containment").checked,
    fuzzy_all: !keepAll && $("#opt-fuzzy-all").checked,
    similarity: Number($("#opt-sim").value),
    min_entries: Math.max(1, Number($("#opt-min").value) || 1),
  };
}

function syncOptionState() {
  const off = $("#opt-keep-all").checked;
  for (const id of ["#opt-fuzzy", "#opt-containment", "#opt-fuzzy-all"]) {
    $(id).disabled = off;
    $(id).closest(".switch").classList.toggle("is-muted", off);
  }
}

function setBusy(busy, label) {
  state.busy = busy;
  const btn = $("#parse-btn");
  btn.disabled = busy;
  if (busy && label) btn.textContent = label;
  else btn.innerHTML = '解析剪藏 <span aria-hidden="true">↘</span>';
  $("#sample-btn").disabled = busy;
  $$("#export-bar button, .detail-actions button").forEach((b) => {
    b.disabled = busy;
  });
}

async function doParse() {
  if (state.busy) return;
  const text = $("#clip-text").value;
  const folder = $("#folder-path").value.trim();
  if (!text.trim() && !state.files.length && !folder) {
    setStatus("还没有内容：粘贴一段剪藏、选个文件，或填文件夹路径。", "is-error");
    $("#clip-text").focus();
    return;
  }

  setBusy(true, "解析中…");
  setStatus("正在解析并去重…");
  try {
    const payload = await fetchJSON("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text.trim() ? text : "",
        files: state.files,
        folder: folder || "",
        options: readOptions(),
      }),
    });

    applyPayload(payload);
    if (payload.stats.entries === 0) {
      setStatus("解析完成，但一条都没剩下——把「只保留 ≥ N 条的书」调小试试。", "is-error");
    }
  } catch (err) {
    setStatus(`解析失败：${err.message}`, "is-error");
  } finally {
    setBusy(false);
  }
}

async function loadSample() {
  if (state.busy) return;
  setBusy(true, "载入中…");
  try {
    const res = await fetch("/api/sample");
    if (!res.ok) throw new Error("示例文件缺失");
    const text = await res.text();
    $("#clip-text").value = text;
    state.files = [];
    chipList([]);
    setBusy(false);
    await doParse();
  } catch (err) {
    setBusy(false);
    setStatus(`载入示例失败：${err.message}`, "is-error");
  }
}

/* ------------------------------------------------------------------ 导出 */

function filenameFrom(header, fallback) {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch (err) {
      /* 落到下面 */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain ? plain[1] : fallback;
}

function triggerDownload(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

const FORMAT_LABELS = { md: "Markdown", pdf: "PDF", docx: "Word" };

/**
 * 导出。
 * @param {object} opts
 *   format     md | pdf | docx
 *   scope      all | selected | book（不给就按「有没有勾选」自动判断）
 *   book       单本导出时的书 id
 *   packaging  merged（合并成一个文档）| per_book（每本单独打包）
 */
async function doExport(opts) {
  const { format } = opts;
  if (!state.token) {
    setStatus("先解析一次，再导出。", "is-error");
    return;
  }
  if (state.busy) return;
  if (!FORMAT_LABELS[format]) return;

  const scope = opts.scope || (state.picked.size ? "selected" : "all");
  const packaging = opts.packaging || state.packaging;
  const payload = { token: state.token, format, scope, packaging };
  if (scope === "book") payload.book = opts.book ?? 0;
  if (scope === "selected") payload.books = Array.from(state.picked);

  if (scope === "selected" && !payload.books.length) {
    setStatus("先勾选要导出的书，或点「全选」。", "is-error");
    return;
  }

  setBusy(true, "导出中…");
  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let message = `导出失败（HTTP ${res.status}）`;
      try {
        const body = await res.json();
        message = body.error || body.detail || message;
      } catch (err) {
        /* 忽略非 JSON 错误体 */
      }
      throw new Error(message);
    }
    const blob = await res.blob();
    const name = filenameFrom(res.headers.get("Content-Disposition"), `剪藏.${format}`);
    triggerDownload(blob, name);

    let where;
    if (scope === "book") {
      where = "当前这本书";
    } else if (scope === "selected") {
      where = `选中的 ${payload.books.length} 本`;
    } else {
      where = `全部 ${state.data ? state.data.books.length : ""} 本`;
    }
    let how = "";
    if (scope !== "book") {
      how = packaging === "per_book" ? "，每本一个文件打成 zip" : "，合并成一个文档";
      if (payload.books && payload.books.length === 1) how = "";
    }
    let extra = "";
    if (format === "pdf" && packaging === "per_book" && scope !== "book") {
      extra = "（每本单独渲染，书多会等久一点）";
    } else if (format === "pdf") {
      extra = "（首次导出要等浏览器渲染，约两三秒）";
    }
    const msg = `${where}的 ${FORMAT_LABELS[format]} 已开始下载：${name}${how}${extra}`;
    setStatus(msg, "is-ok");
  } catch (err) {
    setStatus(`导出失败：${err.message}`, "is-error");
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ 事件 */

function bind() {
  $("#parse-btn").addEventListener("click", doParse);
  $("#sample-btn").addEventListener("click", loadSample);
  $("#hero-sample").addEventListener("click", loadSample);

  $("#opt-keep-all").addEventListener("change", syncOptionState);
  $("#opt-sim").addEventListener("input", () => {
    $("#sim-value").textContent = Number($("#opt-sim").value).toFixed(2);
  });

  $("#pick-file").addEventListener("click", () => $("#file-input").click());
  $("#pick-folder").addEventListener("click", () => $("#folder-input").click());
  $("#pick-kindle").addEventListener("click", async () => {
    if (state.busy) return;
    setBusy(true, "读取中…");
    setStatus("正在查找已连接的 Kindle…");
    try {
      const payload = await fetchJSON("/api/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ detect: true, options: readOptions() }),
      });
      applyPayload(payload);
    } catch (err) {
      setStatus(
        `没读到 Kindle：${err.message} 也可以点「选择文件夹」，手动选 E:\\documents。`,
        "is-error"
      );
    } finally {
      setBusy(false);
    }
  });

  $("#file-input").addEventListener("change", (e) => addFiles(e.target.files));
  $("#folder-input").addEventListener("change", (e) => addFiles(e.target.files));

  $("#read-path").addEventListener("click", () => {
    if (!$("#folder-path").value.trim()) {
      $("#folder-path").focus();
      setStatus("先填一个文件夹路径，例如 E:\\documents。", "is-error");
      return;
    }
    doParse();
  });

  // 拖拽：文件和文件夹都收
  const zone = $("#drop-zone");
  const stop = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  for (const evt of ["dragenter", "dragover"]) {
    zone.addEventListener(evt, (e) => {
      stop(e);
      zone.classList.add("is-drag");
    });
  }
  for (const evt of ["dragleave", "dragend"]) {
    zone.addEventListener(evt, (e) => {
      stop(e);
      zone.classList.remove("is-drag");
    });
  }
  zone.addEventListener("drop", async (e) => {
    stop(e);
    zone.classList.remove("is-drag");
    const items = e.dataTransfer.items;
    if (items && items.length && items[0].webkitGetAsEntry) {
      const out = [];
      for (const item of Array.from(items)) {
        const entry = item.webkitGetAsEntry();
        if (entry) await walkEntry(entry, out, 0);
      }
      if (out.length) {
        await addFiles(out);
        return;
      }
    }
    await addFiles(e.dataTransfer.files);
  });
  // 拖到窗口其它位置时不要触发浏览器打开文件
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  $("#book-grid").addEventListener("click", (e) => {
    // 勾选框只选集，不打开书
    if (e.target.closest(".card-pick")) return;
    const card = e.target.closest(".book-card");
    if (card) openBook(Number(card.dataset.id));
  });
  $("#book-grid").addEventListener("change", (e) => {
    const box = e.target.closest("[data-pick]");
    if (!box) return;
    togglePick(Number(box.dataset.pick), box.checked);
  });
  $("#book-grid").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest(".book-card");
    if (!card) return;
    if (e.target.closest(".card-pick")) return; // 空格交给勾选框自己
    e.preventDefault();
    openBook(Number(card.dataset.id));
  });

  $("#book-detail").addEventListener("click", (e) => {
    if (e.target.closest("#detail-back")) {
      closeBook();
      return;
    }
    const btn = e.target.closest("[data-format]");
    if (!btn) return;
    doExport({ format: btn.dataset.format, scope: "book", book: state.selected ?? 0 });
  });

  $("#export-bar").addEventListener("click", (e) => {
    const pack = e.target.closest("[data-packaging]");
    if (pack) {
      setPackaging(pack.dataset.packaging);
      return;
    }
    const btn = e.target.closest("[data-format]");
    if (!btn) return;
    doExport({ format: btn.dataset.format, packaging: state.packaging });
  });

  $("#pick-all").addEventListener("change", (e) => pickAllBooks(e.target.checked));

  $("#idle-state").addEventListener("click", (e) => {
    const jump = e.target.closest("[data-jump]");
    if (jump) scrollToSection($(jump.dataset.jump));
  });

  let timer = 0;
  $("#search-input").addEventListener("input", (e) => {
    state.query = e.target.value;
    window.clearTimeout(timer);
    timer = window.setTimeout(renderLibrary, 90);
  });

  window.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;
    if (e.key === "/" && !typing) {
      e.preventDefault();
      $("#search-input").focus();
      return;
    }
    if (e.key === "Escape") {
      if (typing) {
        e.target.blur();
      } else if (state.selected !== null) {
        closeBook();
      }
    }
  });

  syncOptionState();
}

/** 目录递归读取（拖文件夹进来时用） */
async function walkEntry(entry, out, depth) {
  if (depth > 5) return;
  if (entry.isFile) {
    if (!/\.txt$/i.test(entry.name)) return;
    try {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      out.push(file);
    } catch (err) {
      /* 跳过读不了的文件 */
    }
    return;
  }
  if (entry.isDirectory) {
    const reader = entry.createReader();
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    for (const child of batch) await walkEntry(child, out, depth + 1);
  }
}

/** /api/parse 的成功结果统一入口（直连 Kindle 与常规解析共用） */
function applyPayload(payload) {
  state.data = payload;
  state.token = payload.token;
  state.selected = null;
  state.picked.clear();
  $("#book-detail").hidden = true;
  $("#book-detail").innerHTML = "";
  $("#search-input").value = "";
  state.query = "";
  renderStats();
  renderLibrary();
  const stats = payload.stats;
  const source = (payload.sources || [])[0] || "剪藏";
  setStatus(
    `解析完成：${stats.books} 本书、${stats.entries} 条剪藏，去掉 ${stats.dropped} 条重复（来源：${clip(source, 40)}）。`,
    "is-ok"
  );
  if (AUTOSCROLL) {
    scrollToSection($("#library"));
    syncNav();
  }
}

/* ------------------------------------------------------------------ 导航高亮 */

const NAV_IDS = ["import", "library", "rules", "formats"];

/**
 * 让导航栏的横线跟着当前所在区块走。
 * 判定方法：以导航栏下沿再往下 24px 为基准线，最后一个越过基准线的区块就是「当前区块」。
 * 还没滚到第一个区块（停在 hero）时，四个标签都不点亮。
 */
function syncNav() {
  const links = $$(".nav-links a");
  if (!links.length) return;

  const header = $(".site-header");
  // 区块落点由 html 的 scroll-padding-top:100px 决定，基准线取导航栏高度再往下一点，
  // 这样刚好滚到位时该区块就算「当前」。
  const line = (header ? header.getBoundingClientRect().height : 84) + 24;
  const doc = document.documentElement;
  let active = "";

  for (const id of NAV_IDS) {
    const section = document.getElementById(id);
    if (!section) continue;
    if (section.getBoundingClientRect().top <= line) active = id;
  }

  // 页面滚到底时最后一节可能还没越过基准线，直接把它点亮
  const scrollable = doc.scrollHeight > window.innerHeight + 100;
  if (scrollable && window.scrollY + window.innerHeight >= doc.scrollHeight - 2) {
    active = NAV_IDS[NAV_IDS.length - 1];
  }

  for (const link of links) {
    const on = link.getAttribute("href") === `#${active}`;
    if (on) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function watchNav() {
  let ticking = false;
  const schedule = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      syncNav();
    });
  };
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  syncNav();
}

/* ------------------------------------------------------------------ 启动 */

/* 自检钩子：带 ?probe=1 打开时，把渲染结果回报给 /api/probe，
   供 tests/ui_check.py 做真断言（无头浏览器读不到 DOM 时的替代方案）。 */
const PROBE = PARAMS.get("probe") === "1";

function reportProbe(extra = {}) {
  if (!PROBE) return;
  const text = (sel) => ($(sel) ? $(sel).textContent.trim() : "");
  const report = {
    cards: document.querySelectorAll(".book-card").length,
    statBooks: text("#stat-books"),
    statEntries: text("#stat-entries"),
    statDropped: text("#stat-dropped"),
    exportHidden: $("#export-bar").hidden,
    gridHidden: $("#book-grid").hidden,
    detailHidden: $("#book-detail").hidden,
    detailTitle: text("#detail-title"),
    entries: document.querySelectorAll("#book-detail .entry").length,
    picked: state.picked.size,
    exportCount: text("#export-count"),
    packaging: state.packaging,
    checked: document.querySelectorAll("#book-grid [data-pick]:checked").length,
    // 当前高亮的导航项（href 去掉 #），用于检查滚动联动
    activeNav: (() => {
      const link = $(".nav-links a[aria-current='page']");
      return link ? link.getAttribute("href").slice(1) : "";
    })(),
    scrollY: Math.round(window.scrollY),
    // 每个标签横线的实际缩放：matrix(1,…) 表示横线完整显示，matrix(0,…) 表示没有
    navUnderlines: $$(".nav-links a").map((a) => {
      const t = getComputedStyle(a, "::after").transform;
      return `${a.getAttribute("href").slice(1)}=${t}`;
    }),
    ...extra,
  };
  fetch("/api/probe", { method: "POST", body: JSON.stringify(report) }).catch(() => {});
}

window.addEventListener("error", (event) => reportProbe({ error: String(event.message) }));

renderStats();
renderLibrary();
bind();
watchNav();

if (PARAMS.get("demo") === "1") {
  loadSample().then(() => {
    // ?pick=1,3 预勾选几本（截图与自检用）
    const pick = PARAMS.get("pick");
    if (pick) {
      state.picked.clear();
      for (const raw of pick.split(",")) {
        const id = Number(raw.trim());
        if (!Number.isNaN(id)) state.picked.add(id);
      }
      renderLibrary();
    }
    if (PARAMS.get("pack")) setPackaging(PARAMS.get("pack"));
    const book = PARAMS.get("book");
    if (book !== null && !Number.isNaN(Number(book))) openBook(Number(book));
    reportProbe();
  });
} else {
  reportProbe();
}

// 带 ?probe=1 时再补报一次：动画、字体、图片会让布局在 load 之后才稳定，
// 上面那一报可能还是首屏状态，这里等布局稳定后再把最终状态送回去。
if (PROBE) {
  window.addEventListener("load", () => {
    window.setTimeout(() => {
      syncNav();
      reportProbe();
    }, 260);
  });
}

// ?scrollto=rules 让页面滚到指定区块再回报（自检用，避免依赖浏览器对 #锚点的处理时机）
const SCROLL_TO = PARAMS.get("scrollto");
if (SCROLL_TO) {
  window.setTimeout(() => {
    scrollToSection(document.getElementById(SCROLL_TO));
    // 横线有 180ms 的过渡，等它走完再回报，不然量到的是过渡起点
    window.setTimeout(() => {
      syncNav();
      reportProbe();
    }, 420);
  }, 320);
}
