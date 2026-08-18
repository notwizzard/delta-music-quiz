/* Студия: загрузка музыки, изготовление заготовок, сборка доски, экспорт игры. */
"use strict";

const S = {
  step: "library",
  library: [], images: [], pack: null, presets: [], prices: [], problems: [], workspace: "",
  openTracks: new Set(),
  selected: new Set(),          // отмеченные треки для наложения
  clip: { start: "", length: 40 },
  saveTimer: null
};

const view = document.getElementById("view");
const dialog = document.getElementById("cell-dialog");
const preview = new Audio();

// ------------------------------------------------------------------ утилиты

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const time = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  document.getElementById("toasts").append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 7000 : 3500);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Ошибка ${response.status}`);
  return data;
}

/* Долгие операции идут фоном: сервер сразу отдаёт задачу, а мы опрашиваем её. */
async function runJob(start, label) {
  let job = await start();
  const node = document.createElement("div");
  node.className = "toast";
  document.getElementById("toasts").append(node);

  try {
    while (job.status === "running") {
      node.innerHTML = `${esc(label)} — ${job.done} из ${job.total}` +
        `<div class="progress"><div style="width:${(job.done / job.total) * 100}%"></div></div>`;
      await new Promise((resolve) => setTimeout(resolve, 400));
      job = await api(`/api/jobs/${job.id}`);
    }
    if (job.status === "failed") throw new Error(job.message);
    return job.result;
  } finally {
    node.remove();
  }
}

function playPreview(variantId, button) {
  if (preview.dataset.id === variantId && !preview.paused) {
    preview.pause();
    return;
  }
  preview.src = `/media/${variantId}`;
  preview.dataset.id = variantId;
  preview.play().catch(() => toast("Не получилось проиграть", "bad"));
}

// ------------------------------------------------------------------ загрузка

async function refresh() {
  const data = await api("/api/state");
  Object.assign(S, data);
  render();
}

// -------------------------------------------------------------- шаг 1: музыка

function renderLibrary() {
  const tracks = S.library.map((track) => {
    const open = S.openTracks.has(track.id);
    const variants = track.variants.map((variant) => `
      <div class="variant ${variant.kind === "source" ? "is-source" : ""}">
        <button class="small" data-play="${variant.id}">▶</button>
        <span class="label">${esc(variant.label)}</span>
        <span class="muted">${time(variant.duration)}</span>
        ${variant.kind === "source" ? "" :
          `<button class="small danger" data-del-variant="${variant.id}">удалить</button>`}
      </div>`).join("");

    const groups = [
      ["Ускорить", "speed"], ["Замедлить", "slow"],
      ["Выше", "pitch_up"], ["Ниже", "pitch_down"]
    ].map(([caption, prefix]) => {
      const options = S.presets.filter((p) => p.name.startsWith(prefix));
      return `<div class="group"><span>${caption}</span>` + options.map((option) =>
        `<button class="small" data-render="${track.id}|${option.name}"
           title="${esc(option.label)} — ${esc(option.hint)}">${esc(option.label.replace(/^\D+/, "").trim())}</button>`
      ).join("") + "</div>";
    }).join("");

    return `
      <div class="track ${open ? "open" : ""}">
        <div class="head">
          <input type="checkbox" class="selector" data-select="${track.id}"
                 ${S.selected.has(track.id) ? "checked" : ""} title="отметить для наложения">
          <div style="flex:1;cursor:pointer" data-toggle="${track.id}">
            <div class="title">${esc(track.display)}</div>
            <div>
              <span class="pill">${time(track.duration)}</span>
              <span class="pill">${track.bpm} BPM</span>
              <span class="pill">${esc(track.key)}</span>
              ${track.shakyBeat ? '<span class="pill warn">бит определён неуверенно</span>' : ""}
              <span class="pill">заготовок: ${track.variants.length}</span>
            </div>
          </div>
          <button class="small" data-toggle="${track.id}">${open ? "свернуть" : "заготовки"}</button>
          <button class="small danger" data-del-track="${track.id}">удалить</button>
        </div>
        <div class="body">
          <div class="tools">
            ${groups}
            <div class="group">
              <button class="small" data-render="${track.id}|reverse">Задом наперёд</button>
              <button class="small" data-render="${track.id}|">Просто фрагмент</button>
            </div>
          </div>
          ${variants}
        </div>
      </div>`;
  }).join("");

  const chosen = S.library.filter((track) => S.selected.has(track.id));

  view.innerHTML = `
    <h2>Шаг 1. Загрузи музыку и сделай из неё заготовки</h2>
    <p class="lead">Заготовка — это кусок трека, обработанный одним из способов.
       Из заготовок потом собирается доска.</p>

    <div class="dropzone" id="dropzone">
      <strong>Перетащи сюда файлы с музыкой</strong>
      или нажми, чтобы выбрать. Подойдут mp3, m4a, wav, flac.
    </div>
    <input type="file" id="file-input" multiple accept="audio/*" hidden>

    <div class="card" style="margin-top:14px">
      <div class="row">
        <label class="field" style="margin:0;flex:1">
          <span>С какой секунды резать (пусто — сама найдёт припев)</span>
          <input id="clip-start" value="${esc(S.clip.start)}" placeholder="автоматически">
        </label>
        <label class="field" style="margin:0;flex:1">
          <span>Длина куска, секунд</span>
          <input id="clip-length" type="number" min="5" max="120" value="${S.clip.length}">
        </label>
      </div>
      <div class="muted">Эти значения используются для всех кнопок обработки ниже.</div>
    </div>

    ${chosen.length >= 2 ? `
      <div class="card">
        <h2>Наложение</h2>
        <p class="muted">Отмечено: ${chosen.map((t) => esc(t.title)).join(", ")}.
           Треки подгонятся под общий темп и тональность и лягут в один бит.</p>
        <div class="row">
          <label class="field" style="margin:0">
            <span>Длина, тактов</span>
            <select id="mashup-bars">
              <option value="4">4 такта</option>
              <option value="8" selected>8 тактов</option>
              <option value="16">16 тактов</option>
            </select>
          </label>
          <label class="field" style="margin:0">
            <span>Тональность</span>
            <select id="mashup-key">
              <option value="1" selected>подогнать под первый трек</option>
              <option value="0">оставить как есть</option>
            </select>
          </label>
          <button class="primary" id="do-mashup">Наложить</button>
        </div>
      </div>` : `
      <p class="muted" style="margin:14px 0 4px">
        Отметь галочками 2–4 трека, чтобы наложить их друг на друга.</p>`}

    ${S.library.length ? tracks : '<p class="muted">Пока пусто. Загрузи первые треки.</p>'}`;

  wireLibrary();
}

function wireLibrary() {
  const zone = document.getElementById("dropzone");
  const input = document.getElementById("file-input");

  zone.onclick = () => input.click();
  input.onchange = () => { upload(input.files); input.value = ""; };
  zone.ondragover = (event) => { event.preventDefault(); zone.classList.add("over"); };
  zone.ondragleave = () => zone.classList.remove("over");
  zone.ondrop = (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    upload(event.dataTransfer.files);
  };

  document.getElementById("clip-start").onchange = (event) => { S.clip.start = event.target.value.trim(); };
  document.getElementById("clip-length").onchange = (event) => { S.clip.length = Number(event.target.value) || 40; };

  const mashupButton = document.getElementById("do-mashup");
  if (mashupButton) mashupButton.onclick = doMashup;
}

async function upload(files) {
  if (!files || !files.length) return;
  const form = new FormData();
  for (const file of files) form.append("files", file);
  try {
    const result = await runJob(
      () => api("/api/tracks", { method: "POST", body: form }),
      "Разбираю треки"
    );
    toast(`Добавлено треков: ${result.added.length}`, "good");
    await refresh();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function clipBody() {
  const start = S.clip.start === "" ? null : Number(S.clip.start);
  return { start: Number.isFinite(start) ? start : null, length: S.clip.length };
}

async function makeVariant(trackId, presetName) {
  try {
    await runJob(() => api(`/api/tracks/${trackId}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset: presetName || null, ...clipBody() })
    }), "Готовлю заготовку");
    S.openTracks.add(trackId);
    await refresh();
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function doMashup() {
  const ids = S.library.filter((track) => S.selected.has(track.id)).map((track) => track.id);
  try {
    const result = await runJob(() => api("/api/mashup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trackIds: ids,
        bars: Number(document.getElementById("mashup-bars").value),
        matchKey: document.getElementById("mashup-key").value === "1"
      })
    }), "Накладываю треки");
    toast(result.label, "good");
    S.openTracks.add(ids[0]);
    S.selected.clear();
    await refresh();
  } catch (error) {
    toast(error.message, "bad");
  }
}

// --------------------------------------------------------------- шаг 2: доска

function allVariantOptions(selectedId) {
  return S.library.map((track) => {
    const options = track.variants.map((variant) =>
      `<option value="${variant.id}" ${variant.id === selectedId ? "selected" : ""}>
         ${esc(variant.label)} · ${time(variant.duration)}</option>`).join("");
    return `<optgroup label="${esc(track.display)}">${options}</optgroup>`;
  }).join("");
}

function variantLabel(variantId) {
  for (const track of S.library) {
    for (const variant of track.variants) {
      if (variant.id === variantId) return `${track.title} · ${variant.label}`;
    }
  }
  return "";
}

function renderBoard() {
  const pack = S.pack;
  const themes = pack.themes.map((theme, themeIndex) => {
    const cells = theme.questions.map((question, questionIndex) => `
      <button class="cell-btn ${question.variantId ? "" : "empty"}"
              data-cell="${themeIndex}|${questionIndex}">
        <span class="price">${question.price}</span>
        <span class="what">${question.variantId ? esc(variantLabel(question.variantId)) : "выбрать звук"}</span>
        <span class="what">${question.answer ? esc(question.answer) : "ответ не задан"}${question.imageId ? " · 🖼" : ""}</span>
      </button>`).join("");

    return `
      <div class="theme-row">
        <div class="theme-head">
          <input value="${esc(theme.title)}" data-theme-title="${themeIndex}" placeholder="Название темы">
          <button class="small" data-add-cell="${themeIndex}">+ клетка</button>
          <button class="small danger" data-del-theme="${themeIndex}">удалить тему</button>
        </div>
        <div class="cells">${cells}</div>
      </div>`;
  }).join("");

  const teams = pack.teams.map((name, index) => `
    <div class="row" style="margin-bottom:6px">
      <input value="${esc(name)}" data-team="${index}" style="flex:1">
      <button class="small danger" data-del-team="${index}" ${pack.teams.length <= 2 ? "disabled" : ""}>убрать</button>
    </div>`).join("");

  view.innerHTML = `
    <h2>Шаг 2. Собери доску</h2>
    <p class="lead">Темы идут строками, клетки — по возрастанию цены.
       Нажми на клетку, чтобы выбрать звук и вписать ответ.</p>

    <div class="card">
      <label class="field"><span>Название игры</span>
        <input id="pack-title" value="${esc(pack.title)}"></label>
      <label class="field" style="margin-bottom:6px"><span>Команды</span></label>
      ${teams}
      <button class="small" id="add-team">+ команда</button>
    </div>

    <div class="themes">${themes || '<p class="muted">Тем пока нет.</p>'}</div>
    <button class="primary" id="add-theme" style="margin-top:12px">+ Добавить тему</button>`;

  wireBoard();
}

function wireBoard() {
  document.getElementById("pack-title").oninput = (event) => {
    S.pack.title = event.target.value;
    savePack();
  };
  document.getElementById("add-theme").onclick = () => {
    S.pack.themes.push({
      title: "Новая тема",
      questions: S.prices.map((price) => ({
        price, variantId: "", answer: "", answerVariantId: null, comment: "", imageId: null, imageWhen: "answer"
      }))
    });
    savePack(true);
  };
  document.getElementById("add-team").onclick = () => {
    S.pack.teams.push(`Команда ${S.pack.teams.length + 1}`);
    savePack(true);
  };
}

function openCell(themeIndex, questionIndex) {
  const question = S.pack.themes[themeIndex].questions[questionIndex];
  document.getElementById("cell-dialog-body").innerHTML = `
    <h2>${esc(S.pack.themes[themeIndex].title)} · ${question.price}</h2>
    <label class="field"><span>Цена</span>
      <input type="number" step="50" id="c-price" value="${question.price}"></label>
    <label class="field"><span>Что играет в вопросе</span>
      <select id="c-variant"><option value="">— не выбрано —</option>
        ${allVariantOptions(question.variantId)}</select></label>
    <div class="row" style="margin:-6px 0 12px">
      <button class="small" id="c-play" ${question.variantId ? "" : "disabled"}>▶ Послушать</button>
      <span class="muted">Во время игры ведущий сможет перематывать этот звук как угодно</span>
    </div>
    <label class="field"><span>Правильный ответ</span>
      <input id="c-answer" value="${esc(question.answer)}" placeholder="Исполнитель — Название"></label>
    <label class="field"><span>Что играет, когда ответ показан</span>
      <select id="c-answer-variant"><option value="">— ничего —</option>
        ${allVariantOptions(question.answerVariantId)}</select></label>
    <label class="field"><span>Заметка ведущему (необязательно)</span>
      <textarea id="c-comment" placeholder="Что сказать вслух, год выпуска, факт">${esc(question.comment)}</textarea></label>

    <label class="field" style="margin-bottom:6px"><span>Картинка (необязательно)</span></label>
    <div id="c-image-block"></div>
    <input type="file" id="c-image-file" accept="image/*" hidden>

    <div class="row">
      <button class="danger" id="c-clear">Очистить клетку</button>
      <span class="spacer"></span>
      <button class="ghost" id="c-cancel">Отмена</button>
      <button class="primary" id="c-save">Сохранить</button>
    </div>`;

  const variantSelect = document.getElementById("c-variant");
  const answerSelect = document.getElementById("c-answer-variant");
  const draft = { imageId: question.imageId || "", imageWhen: question.imageWhen || "answer" };

  /* Блок картинки перерисовывается сам по себе: после загрузки нового файла
     список должен обновиться, не закрывая диалог и не теряя остальные поля. */
  function paintImage() {
    const options = S.images.map((image) =>
      `<option value="${image.id}" ${image.id === draft.imageId ? "selected" : ""}>${esc(image.label)}</option>`).join("");

    document.getElementById("c-image-block").innerHTML = `
      <div class="row" style="margin-bottom:10px">
        <select id="c-image" style="flex:1">
          <option value="">— без картинки —</option>${options}
        </select>
        <button class="small" id="c-image-add">Загрузить свою</button>
      </div>
      ${draft.imageId ? `
        <div class="image-preview">
          <img src="/media/image/${draft.imageId}" alt="">
          <div style="flex:1">
            <label class="field" style="margin:0"><span>Когда показать залу</span>
              <select id="c-image-when">
                <option value="answer" ${draft.imageWhen === "answer" ? "selected" : ""}>вместе с ответом</option>
                <option value="question" ${draft.imageWhen === "question" ? "selected" : ""}>сразу, в вопросе</option>
              </select></label>
          </div>
        </div>` : `<div class="muted" style="margin-bottom:10px">Картинки нет.</div>`}`;

    document.getElementById("c-image").onchange = (event) => {
      draft.imageId = event.target.value;
      paintImage();
    };
    document.getElementById("c-image-add").onclick = () => document.getElementById("c-image-file").click();
    const when = document.getElementById("c-image-when");
    if (when) when.onchange = (event) => { draft.imageWhen = event.target.value; };
  }

  document.getElementById("c-image-file").onchange = async (event) => {
    const files = event.target.files;
    event.target.value = "";
    if (!files || !files.length) return;
    const form = new FormData();
    for (const file of files) form.append("files", file);
    try {
      const result = await api("/api/images", { method: "POST", body: form });
      S.images = (await api("/api/state")).images;
      if (result.added.length) draft.imageId = result.added[0].id;
      paintImage();
    } catch (error) {
      toast(error.message, "bad");
    }
  };

  paintImage();

  /* Ответом почти всегда должен звучать оригинал того же трека —
     подставляем его сразу, чтобы это не приходилось делать руками. */
  variantSelect.onchange = () => {
    document.getElementById("c-play").disabled = !variantSelect.value;
    if (answerSelect.value) return;
    for (const track of S.library) {
      if (track.variants.some((variant) => variant.id === variantSelect.value)) {
        const original = track.variants.find((variant) => variant.kind === "source");
        if (original) answerSelect.value = original.id;
        return;
      }
    }
  };

  document.getElementById("c-play").onclick = () => playPreview(variantSelect.value);
  document.getElementById("c-cancel").onclick = () => dialog.close();
  document.getElementById("c-clear").onclick = () => {
    Object.assign(question, { variantId: "", answer: "", answerVariantId: null, comment: "", imageId: null, imageWhen: "answer" });
    dialog.close();
    savePack(true);
  };
  document.getElementById("c-save").onclick = () => {
    question.variantId = variantSelect.value;
    question.price = Number(document.getElementById("c-price").value) || question.price;
    question.answer = document.getElementById("c-answer").value.trim();
    question.answerVariantId = answerSelect.value || null;
    question.comment = document.getElementById("c-comment").value.trim();
    question.imageId = draft.imageId || null;
    question.imageWhen = draft.imageWhen;
    dialog.close();
    savePack(true);
  };

  dialog.showModal();
}

// -------------------------------------------------------------- шаг 3: экспорт

function renderExport() {
  const ready = S.problems.length === 0;
  const count = S.pack.themes.reduce((sum, theme) => sum + theme.questions.length, 0);

  view.innerHTML = `
    <h2>Шаг 3. Собери готовую игру</h2>
    <p class="lead">На выходе получится один файл. Его можно скопировать на любой
       ноутбук и открыть двойным кликом — ни интернет, ни установка не нужны.</p>

    <div class="card ${ready ? "ok-banner" : "problems"}">
      ${ready
        ? `<strong>Всё готово.</strong> Тем: ${S.pack.themes.length}, вопросов: ${count}, команд: ${S.pack.teams.length}.`
        : `<strong>Осталось поправить:</strong><ul>${S.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`}
    </div>

    <button class="primary" id="do-export" ${ready ? "" : "disabled"}>Собрать игру</button>
    <div id="export-result"></div>

    <div class="card" style="margin-top:20px">
      <h2>Как вести игру</h2>
      <ol class="muted" style="padding-left:20px;line-height:1.7">
        <li>Открой скачанный файл двойным кликом — откроется пульт ведущего.</li>
        <li>Подключи проектор или телевизор вторым экраном (именно вторым, не зеркалом).</li>
        <li>Нажми «Открыть экран для зала» и перетащи новое окно на большой экран, затем разверни его.</li>
        <li>На своём экране остаётся пульт: там видны ответы, здесь же начисляются баллы.</li>
        <li>Нажми на клетку и включай звук. Перематывать можно куда угодно — тяни ползунок или жми стрелки.</li>
        <li>Команда жмёт свою цифру на клавиатуре, или ты нажимаешь её сам. Дальше «Верно» или «Неверно».</li>
      </ol>
    </div>`;

  document.getElementById("do-export").onclick = doExport;
}

async function doExport() {
  try {
    const result = await runJob(() => api("/api/export", { method: "POST" }), "Собираю игру");
    document.getElementById("export-result").innerHTML = `
      <div class="card ok-banner" style="margin-top:12px">
        <strong>Готово.</strong> Вопросов: ${result.questions}, звуков внутри: ${result.clips},
        размер файла: ${result.sizeMb} МБ.
        <div style="margin-top:10px">
          <a href="/api/download/${encodeURIComponent(result.file)}" download>
            <button class="primary">Скачать ${esc(result.file)}</button></a>
        </div>
        <div class="muted" style="margin-top:8px">Файл также лежит в папке exports рабочей папки студии.</div>
      </div>`;
    toast("Игра собрана", "good");
  } catch (error) {
    toast(error.message, "bad");
  }
}

// -------------------------------------------------------------- сохранение

function savePack(immediate = false) {
  clearTimeout(S.saveTimer);
  const send = async () => {
    try {
      const data = await api("/api/pack", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(S.pack)
      });
      S.pack = data.pack;
      S.problems = data.problems;
      if (immediate) render();
    } catch (error) {
      toast(error.message, "bad");
    }
  };
  if (immediate) send(); else S.saveTimer = setTimeout(send, 600);
}

// ------------------------------------------------------------------ события

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-step],[data-toggle],[data-play],[data-render],"
    + "[data-del-track],[data-del-variant],[data-cell],[data-add-cell],[data-del-theme],[data-del-team]");
  if (!target) return;
  const d = target.dataset;

  if (d.step) { S.step = d.step; render(); }
  else if (d.toggle) {
    S.openTracks.has(d.toggle) ? S.openTracks.delete(d.toggle) : S.openTracks.add(d.toggle);
    render();
  }
  else if (d.play) playPreview(d.play, target);
  else if (d.render !== undefined) {
    const [trackId, presetName] = d.render.split("|");
    makeVariant(trackId, presetName);
  }
  else if (d.delTrack) confirmDelete("Удалить трек вместе со всеми его заготовками?",
    () => api(`/api/tracks/${d.delTrack}`, { method: "DELETE" }));
  else if (d.delVariant) confirmDelete("Удалить заготовку?",
    () => api(`/api/variants/${d.delVariant}`, { method: "DELETE" }));
  else if (d.cell) { const [t, q] = d.cell.split("|").map(Number); openCell(t, q); }
  else if (d.addCell) {
    const theme = S.pack.themes[Number(d.addCell)];
    const last = theme.questions[theme.questions.length - 1];
    theme.questions.push({
      price: last ? last.price + 100 : 100,
      variantId: "", answer: "", answerVariantId: null, comment: "", imageId: null, imageWhen: "answer"
    });
    savePack(true);
  }
  else if (d.delTheme) {
    if (window.confirm("Удалить тему целиком?")) {
      S.pack.themes.splice(Number(d.delTheme), 1);
      savePack(true);
    }
  }
  else if (d.delTeam) { S.pack.teams.splice(Number(d.delTeam), 1); savePack(true); }
});

document.addEventListener("change", (event) => {
  const select = event.target.dataset.select;
  if (select) {
    event.target.checked ? S.selected.add(select) : S.selected.delete(select);
    render();
  }
});

document.addEventListener("input", (event) => {
  const d = event.target.dataset;
  if (d.themeTitle !== undefined) { S.pack.themes[Number(d.themeTitle)].title = event.target.value; savePack(); }
  else if (d.team !== undefined) { S.pack.teams[Number(d.team)] = event.target.value; savePack(); }
});

async function confirmDelete(question, action) {
  if (!window.confirm(question)) return;
  try {
    await action();
    await refresh();
  } catch (error) {
    toast(error.message, "bad");
  }
}

function render() {
  document.querySelectorAll("[data-step]").forEach((button) =>
    button.classList.toggle("active", button.dataset.step === S.step));
  ({ library: renderLibrary, board: renderBoard, export: renderExport }[S.step])();
}

refresh().catch((error) => {
  view.innerHTML = `<div class="card problems">Не удалось связаться со студией: ${esc(error.message)}</div>`;
});
