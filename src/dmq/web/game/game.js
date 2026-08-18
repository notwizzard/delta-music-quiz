/* Логика игры: пульт ведущего в основном окне, экран зала в дочернем.
 *
 * Почему дочернее окно, а не два независимо открытых файла: окно, созданное
 * через window.open(''), наследует origin родителя, поэтому родитель может
 * писать прямо в его DOM. Это единственный способ синхронизировать два экрана,
 * когда файл открыт с диска (file://) — там и BroadcastChannel, и localStorage,
 * и postMessage между отдельными окнами не работают из-за непрозрачного origin.
 */
(function () {
  "use strict";

  var PACK = JSON.parse(document.getElementById("pack-data").textContent);
  var BUZZ_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];
  var NUDGE = 5;  // на сколько секунд прыгают стрелки

  // ---------------------------------------------------------------- состояние

  var state = {
    view: "board",
    themeIndex: 0,
    questionIndex: 0,
    used: {},          // "тема:вопрос" -> true
    scores: PACK.teams.map(function () { return 0; }),
    playing: false,
    position: 0,
    buzzed: null,      // индекс команды, нажавшей кнопку
    spent: {},         // команды, уже ответившие неверно на этом вопросе
    answerShown: false,
    audioProblem: false,
    stageBlocked: false
  };

  // ------------------------------------------------------------------- звук

  var blobUrls = {};
  var player = new Audio();
  var currentUrl = null;
  var frame = null;

  function audioUrl(key, base64) {
    if (blobUrls[key]) return blobUrls[key];
    var binary = atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    blobUrls[key] = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
    return blobUrls[key];
  }

  /* Ползунок двигаем по кадрам, а не по событию timeupdate: оно приходит
     несколько раз в секунду, и головка ощутимо дёргается. */
  function tick() {
    if (!state.playing) return;
    state.position = player.currentTime;
    paintPosition();
    frame = requestAnimationFrame(tick);
  }

  player.addEventListener("ended", function () {
    state.playing = false;
    state.position = trackLength();
    vizStop();
    render();
  });

  function attach(url) {
    if (currentUrl === url) return;
    currentUrl = url;
    player.src = url;
    state.position = 0;
  }

  function start(from) {
    state.audioProblem = false;
    var begin = function () {
      if (from !== null && from !== undefined) {
        try { player.currentTime = from; } catch (error) { /* до загрузки перемотка недоступна */ }
      }
      player.play().then(function () {
        state.playing = true;
        if (frame) cancelAnimationFrame(frame);
        frame = requestAnimationFrame(tick);
        vizStart();
        render();
      }).catch(function () {
        state.playing = false;
        state.audioProblem = true;
        render();
      });
    };

    if (vizOn) audioGraph();

    var go = function () {
      if (player.readyState >= 1) begin();
      else player.addEventListener("loadedmetadata", begin, { once: true });
    };

    /* Спящий контекст нужно разбудить до воспроизведения: элемент уже
       перенаправлен в граф, и на спящем контексте зал не услышит ничего.
       Пробуждение делаем всегда, когда граф существует, — даже если
       визуализацию потом выключили, звук всё равно идёт через него. */
    var waking = graph && graph.context.state !== "running" ? graph.context.resume() : null;
    if (waking && typeof waking.then === "function") waking.then(go, go);
    else go();
  }

  function pause() {
    if (frame) cancelAnimationFrame(frame);
    frame = null;
    player.pause();
    state.playing = false;
    vizStop();
  }

  // ------------------------------------------------------------ визуализация

  /* Разбор звука идёт живьём через AnalyserNode, а не по заранее посчитанным
     долям. Причина в наших же преобразованиях: замедление в четыре раза даёт
     тридцать ударов в минуту, реверс превращает удар в хвост наоборот — любая
     заготовленная сетка на таком материале врала бы. Живой анализ показывает
     ровно то, что слышит зал.

     Вся визуализация необязательна: если Web Audio почему-то не заводится,
     graph становится false, картинки нет, а звук играет как ни в чём не бывало. */

  var VIZ_KEY = "dmq:viz";
  var vizOn = true;
  try { vizOn = localStorage.getItem(VIZ_KEY) !== "off"; } catch (error) { /* не критично */ }

  var graph = null;   // null — ещё не пробовали, false — не получилось

  function audioGraph() {
    if (graph !== null) return graph;
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) throw new Error("нет Web Audio");

      var context = new Ctx();
      var analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.75;

      /* Порядок здесь важен. createMediaElementSource навсегда уводит звук
         элемента в граф, поэтому путь до колонок должен быть готов заранее:
         если что-то упадёт после перенаправления, игра останется без звука. */
      analyser.connect(context.destination);
      context.createMediaElementSource(player).connect(analyser);

      graph = {
        context: context,
        analyser: analyser,
        data: new Uint8Array(analyser.frequencyBinCount)
      };
    } catch (error) {
      graph = false;
    }
    return graph;
  }

  var viz = {
    frame: null, angle: 0, bass: 0, level: 0, bright: 0,
    sectors: 10, points: 72, calm: false, lite: false, slowFrames: 0
  };

  function vizCanvas() {
    return stageAlive() ? stage.document.getElementById("viz") : null;
  }

  function vizResize() {
    var canvas = vizCanvas();
    if (!canvas) return;
    // Экран зала бывает 4K, но заливать столько пикселей незачем — упираемся в 1920.
    var ratio = Math.min(stage.devicePixelRatio || 1, 2);
    var width = Math.min(stage.innerWidth, 1920);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(stage.innerHeight * (width / stage.innerWidth) * ratio);
  }

  function vizStart() {
    if (!vizOn || viz.frame !== null) return;
    if (!audioGraph() || !vizCanvas()) return;

    /* Визуализацию могут включить впервые прямо посреди играющего вопроса.
       Звук в этот момент уже уходит в граф, и спящий контекст оборвал бы его. */
    if (graph.context.state !== "running") graph.context.resume();

    viz.calm = !!(stage.matchMedia && stage.matchMedia("(prefers-reduced-motion: reduce)").matches);
    vizResize();
    viz.frame = stage.requestAnimationFrame(vizDraw);
  }

  function vizStop() {
    if (viz.frame !== null && stageAlive()) stage.cancelAnimationFrame(viz.frame);
    viz.frame = null;
  }

  function vizClear() {
    vizStop();
    var canvas = vizCanvas();
    if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
  }

  function band(data, from, to) {
    var sum = 0;
    for (var i = from; i < to; i++) sum += data[i];
    return sum / ((to - from) * 255);
  }

  function vizDraw(stamp) {
    var canvas = vizCanvas();
    if (!canvas || !graph || !vizOn) { viz.frame = null; return; }

    var started = stage.performance ? stage.performance.now() : 0;
    var paint = canvas.getContext("2d");
    var width = canvas.width, height = canvas.height;
    var data = graph.data;
    graph.analyser.getByteFrequencyData(data);

    var bass = band(data, 1, 8);       // примерно до 170 Гц — бочка и бас
    var mid = band(data, 8, 48);
    var high = band(data, 48, 180);
    var loud = (bass + mid + high) / 3;

    // Сглаживание разной инерции: пульс отзывчивый, цвет ленивый.
    viz.bass += (bass - viz.bass) * (viz.calm ? 0.08 : 0.3);
    viz.level += (loud - viz.level) * 0.14;
    viz.bright += ((mid + high * 1.6) / (bass + mid + high + 0.001) - viz.bright) * 0.05;

    // След вместо очистки: движение оставляет за собой затухающий шлейф.
    paint.globalCompositeOperation = "source-over";
    paint.fillStyle = "rgba(16, 16, 26, " + (viz.calm ? 0.34 : 0.17) + ")";
    paint.fillRect(0, 0, width, height);

    viz.angle += (viz.calm ? 0.0005 : 0.0015) + viz.level * 0.0035;

    var span = Math.min(width, height);
    var inner = span * (0.20 + viz.bass * 0.055);  // середина остаётся свободной под текст
    var reach = span * 0.31;

    /* Цвет ведём по короткой дуге фиолетовый → малиновый → янтарный: это ровно
       палитра игры. Линейная развёртка в другую сторону прошла бы через
       зелёный, который здесь смотрится чужеродно. */
    var hue = (265 + Math.min(1, viz.bright * 1.7) * 138) % 360;

    var sectors = viz.lite ? 6 : viz.sectors;
    var points = viz.lite ? 32 : viz.points;

    /* Профиль лепестка сглаживается дважды: по времени, чтобы он не дёргался
       от кадра к кадру, и по соседним точкам, чтобы вместо колючих игл
       получались плавные волны. Без этого мандала выглядит как колючее солнце. */
    if (!viz.profile || viz.profile.length !== points + 1) viz.profile = new Float32Array(points + 1);
    var profile = viz.profile;
    for (var i = 0; i <= points; i++) {
      var bin = 2 + Math.round(Math.pow(i / points, 1.4) * 150);
      var raw = (data[bin] + data[bin + 1] + data[bin + 2]) / 765;
      profile[i] += (raw - profile[i]) * (viz.calm ? 0.08 : 0.22);
    }

    var wedge = (2 * Math.PI) / sectors;
    var radius = function (i) {
      var a = profile[Math.max(0, i - 1)], b = profile[i], c = profile[Math.min(points, i + 1)];
      return inner + ((a + b + b + c) / 4) * reach;
    };

    paint.save();
    paint.translate(width / 2, height / 2);
    paint.rotate(viz.angle);
    paint.globalCompositeOperation = "lighter";
    paint.lineWidth = Math.max(1, span * 0.0016);
    paint.lineJoin = "round";

    var alpha = Math.min(0.34, (viz.calm ? 0.08 : 0.13) + viz.level * 0.22);

    for (var s = 0; s < sectors; s++) {
      paint.save();
      paint.rotate(s * wedge);
      if (s % 2) paint.scale(1, -1);           // зеркалим через один — отсюда калейдоскоп
      paint.beginPath();
      for (var k = 0; k <= points; k++) {
        var angle = (k / points) * wedge;
        var r = radius(k);
        var x = Math.cos(angle) * r, y = Math.sin(angle) * r;
        if (k) paint.lineTo(x, y); else paint.moveTo(x, y);
      }
      // Возврат по внутренней окружности замыкает лепесток.
      for (var back = points; back >= 0; back--) {
        var ba = (back / points) * wedge;
        paint.lineTo(Math.cos(ba) * inner, Math.sin(ba) * inner);
      }
      paint.closePath();
      paint.fillStyle = "hsla(" + hue.toFixed(0) + ", 70%, 44%, " + (alpha * 0.34).toFixed(3) + ")";
      paint.fill();
      paint.strokeStyle = "hsla(" + hue.toFixed(0) + ", 80%, 56%, " + alpha.toFixed(3) + ")";
      paint.stroke();
      paint.restore();
    }

    // Дышащее кольцо по внутренней границе — оно держит общий ритм картинки.
    paint.beginPath();
    paint.arc(0, 0, inner * (0.93 + viz.bass * 0.07), 0, 2 * Math.PI);
    paint.strokeStyle = "hsla(" + (hue - 18).toFixed(0) + ", 70%, 54%, " + (alpha * 0.85).toFixed(3) + ")";
    paint.lineWidth = Math.max(1, span * 0.0022);
    paint.stroke();
    paint.restore();

    /* Текст зала лежит поверх холста ровно в центре, поэтому середину
       притемняем — иначе цена и подписи тонут в свечении. */
    var veil = paint.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, inner * 1.15);
    veil.addColorStop(0, "rgba(16, 16, 26, 0.72)");
    veil.addColorStop(1, "rgba(16, 16, 26, 0)");
    paint.globalCompositeOperation = "source-over";
    paint.fillStyle = veil;
    paint.fillRect(0, 0, width, height);

    /* Игру ведут с чужого ноутбука, и просадка до пятнадцати кадров хуже, чем
       упрощённая картинка. Если кадры стабильно тяжёлые — один раз облегчаем. */
    if (started && !viz.lite) {
      if ((stage.performance.now() - started) > 12) viz.slowFrames++;
      else viz.slowFrames = Math.max(0, viz.slowFrames - 1);
      if (viz.slowFrames > 45) viz.lite = true;
    }

    viz.frame = stage.requestAnimationFrame(vizDraw);
  }

  function toggleViz() {
    vizOn = !vizOn;
    try { localStorage.setItem(VIZ_KEY, vizOn ? "on" : "off"); } catch (error) { /* не критично */ }
    if (vizOn && state.playing) vizStart(); else vizClear();
    render();
  }

  // --------------------------------------------------------------- выборки

  function currentQuestion() {
    var theme = PACK.themes[state.themeIndex];
    return theme ? theme.questions[state.questionIndex] : null;
  }

  function trackLength() {
    var question = currentQuestion();
    if (!question) return 0;
    if (state.answerShown && question.answerDuration) return question.answerDuration;
    if (isFinite(player.duration) && player.duration > 0) return player.duration;
    return question.duration || 0;
  }

  // --------------------------------------------------------------- действия

  function openQuestion(themeIndex, questionIndex) {
    if (state.used[themeIndex + ":" + questionIndex]) return;
    pause();
    state.view = "question";
    state.themeIndex = themeIndex;
    state.questionIndex = questionIndex;
    state.position = 0;
    state.buzzed = null;
    state.spent = {};
    state.answerShown = false;
    vizClear();

    var question = currentQuestion();
    attach(audioUrl(question.audioKey, question.audio));
    render();
  }

  function togglePlay() {
    if (state.playing) {
      pause();
      render();
      return;
    }
    // Доиграли до конца — следующее нажатие начинает сначала.
    var from = state.position >= trackLength() - 0.05 ? 0 : state.position;
    start(from);
    render();
  }

  /* Перемотка не перерисовывает экран целиком, а только двигает полосу.
     Полная перерисовка заменила бы саму полосу новым элементом, и тогда
     перетаскивание головки обрывалось бы после первого же движения мыши. */
  function seekTo(seconds) {
    var length = trackLength();
    state.position = Math.max(0, Math.min(seconds, length));
    try { player.currentTime = state.position; } catch (error) { /* ещё не загрузилось */ }
    paintPosition();
  }

  function nudge(delta) {
    seekTo(state.position + delta);
  }

  function restart() {
    seekTo(0);
    if (!state.playing) start(0);
  }

  function buzz(teamIndex) {
    if (state.view !== "question" || state.answerShown) return;
    if (state.buzzed !== null || state.spent[teamIndex]) return;
    pause();
    state.buzzed = teamIndex;
    render();
  }

  function judge(teamIndex, correct) {
    var question = currentQuestion();
    if (!question) return;
    if (correct) {
      state.scores[teamIndex] += question.price;
      showAnswer();
    } else {
      if (PACK.wrongAnswerPenalty) state.scores[teamIndex] -= question.price;
      state.spent[teamIndex] = true;
      state.buzzed = null;
      render();
    }
    persist();
  }

  function showAnswer() {
    pause();
    state.answerShown = true;
    state.position = 0;
    render();

    var question = currentQuestion();
    if (question && question.answerAudio) {
      attach(audioUrl(question.answerAudioKey, question.answerAudio));
      start(0);
    }
  }

  function closeQuestion() {
    pause();
    vizClear();
    state.used[state.themeIndex + ":" + state.questionIndex] = true;
    state.view = "board";
    state.buzzed = null;
    state.answerShown = false;
    persist();
    render();
  }

  function adjustScore(teamIndex, delta) {
    state.scores[teamIndex] += delta;
    persist();
    render();
  }

  function resetGame() {
    if (!window.confirm("Сбросить счёт и открыть все вопросы заново?")) return;
    pause();
    state.used = {};
    state.scores = PACK.teams.map(function () { return 0; });
    state.view = "board";
    state.buzzed = null;
    state.answerShown = false;
    persist();
    render();
  }

  /* Счёт переживает случайное закрытие вкладки. На file:// хранилище может быть
     недоступно — тогда просто работаем без сохранения. */
  function persist() {
    try {
      localStorage.setItem("dmq:" + PACK.title, JSON.stringify({ used: state.used, scores: state.scores }));
    } catch (error) { /* не критично */ }
  }

  function restore() {
    try {
      var saved = JSON.parse(localStorage.getItem("dmq:" + PACK.title) || "null");
      if (saved && saved.scores && saved.scores.length === PACK.teams.length) {
        state.used = saved.used || {};
        state.scores = saved.scores;
      }
    } catch (error) { /* не критично */ }
  }

  // ------------------------------------------------------------ дочернее окно

  var stage = null;

  function openStage() {
    /* Предыдущее окно зала могли просто закрыть крестиком — тогда цикл
       отрисовки оборвался вместе с ним, но его номер кадра остался записан.
       Без сброса запуск решил бы, что анимация уже идёт, и в новом окне
       визуализация не ожила бы. */
    viz.frame = null;

    stage = window.open("", "dmq-stage", "width=1280,height=720");
    if (!stage) {
      // Единственный способ для ведущего капитально застрять, поэтому объясняем
      // прямо на экране и не убираем, пока окно не откроется.
      state.stageBlocked = true;
      render();
      return;
    }
    state.stageBlocked = false;

    var css = document.getElementById("app-style").textContent;
    stage.document.open();
    stage.document.write(
      '<!doctype html><html lang="ru"><head><meta charset="utf-8">' +
      "<title>" + escapeHtml(PACK.title) + "</title><style>" + css + "</style></head>" +
      '<body><canvas id="viz"></canvas><div id="root"></div></body></html>'
    );
    stage.document.close();
    stage.focus();
    stage.addEventListener("resize", vizResize);
    if (state.playing) vizStart();
    render();
  }

  function stageAlive() {
    return stage && !stage.closed && stage.document && stage.document.getElementById("root");
  }

  window.addEventListener("beforeunload", function () {
    if (stage && !stage.closed) stage.close();
  });

  // -------------------------------------------------------------- отрисовка

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
    });
  }

  function clock(seconds) {
    var whole = Math.max(0, Math.floor(seconds || 0));
    return Math.floor(whole / 60) + ":" + ("0" + (whole % 60)).slice(-2);
  }

  function boardHtml(forStage) {
    var columns = PACK.themes.reduce(function (most, theme) {
      return Math.max(most, theme.questions.length);
    }, 0);

    var rows = PACK.themes.map(function (theme, themeIndex) {
      var cells = theme.questions.map(function (question, questionIndex) {
        var isUsed = state.used[themeIndex + ":" + questionIndex];
        var attributes = forStage || isUsed
          ? ""
          : ' data-open="' + themeIndex + "," + questionIndex + '"';
        return (
          '<button class="cell' + (isUsed ? " used" : "") + '"' + attributes +
          (isUsed ? " disabled" : "") + ">" + (isUsed ? "" : question.price) + "</button>"
        );
      }).join("");

      // Добиваем пустыми клетками, чтобы строки не разъезжались.
      var filler = "";
      for (var i = theme.questions.length; i < columns; i++) filler += '<div class="cell used"></div>';

      return '<div class="row"><div class="theme">' + escapeHtml(theme.title) + "</div>" + cells + filler + "</div>";
    }).join("");

    return '<div class="board" style="--cols:' + columns + '">' + rows + "</div>";
  }

  function seekHtml(interactive) {
    return (
      '<div class="seek' + (interactive ? " grab" : "") + '"' + (interactive ? " data-seek" : "") + ">" +
      '<div class="seek-fill" data-seek-fill></div>' +
      '<div class="seek-head" data-seek-head></div>' +
      "</div>" +
      '<div class="seek-time"><span data-seek-now>' + clock(state.position) + "</span>" +
      "<span>" + clock(trackLength()) + "</span></div>"
    );
  }

  /* Ползунок двигаем стилями напрямую, без перерисовки всего экрана: иначе на
     каждом кадре пересобирался бы весь DOM обоих окон. */
  function paintPosition() {
    var length = trackLength();
    var ratio = length > 0 ? Math.max(0, Math.min(1, state.position / length)) : 0;
    var percent = ratio * 100 + "%";
    var documents = [document];
    if (stageAlive()) documents.push(stage.document);

    for (var i = 0; i < documents.length; i++) {
      var fill = documents[i].querySelector("[data-seek-fill]");
      var head = documents[i].querySelector("[data-seek-head]");
      var now = documents[i].querySelector("[data-seek-now]");
      if (fill) fill.style.width = percent;
      if (head) head.style.left = percent;
      if (now) now.textContent = clock(state.position);
    }
  }

  function teamsHtml(forStage) {
    return '<div class="' + (forStage ? "stage-scores" : "teams") + '">' + PACK.teams.map(function (name, index) {
      var classes = "team";
      if (state.buzzed === index) classes += " buzzed";
      if (state.spent[index]) classes += " spent";
      var score = state.scores[index];
      var controls = forStage ? "" :
        '<div class="controls">' +
        '<button data-score="' + index + ',-100">−100</button>' +
        '<button data-score="' + index + ',100">+100</button>' +
        "</div>";
      return (
        '<div class="' + classes + '">' +
        '<div class="name"><span>' + escapeHtml(name) + "</span>" +
        (forStage ? "" : '<span class="key">' + BUZZ_KEYS[index] + "</span>") + "</div>" +
        '<div class="score' + (score < 0 ? " minus" : "") + '">' + score + "</div>" +
        controls + "</div>"
      );
    }).join("") + "</div>";
  }

  function renderAdmin() {
    var connected = stageAlive();
    var head =
      '<div class="topbar"><h1>' + escapeHtml(PACK.title) + "</h1>" +
      '<span class="badge' + (connected ? " live" : "") + '">' +
      (connected ? "Экран зала подключён" : "Экран зала не открыт") + "</span>" +
      '<button data-action="stage">' + (connected ? "Показать окно зала" : "Открыть экран для зала") + "</button>" +
      '<button data-action="viz">' + (vizOn ? "Визуализация: вкл" : "Визуализация: выкл") + "</button>" +
      '<button data-action="reset">Сбросить игру</button></div>';

    var warning = state.stageBlocked
      ? '<div class="notice">Браузер не дал открыть окно для зала. ' +
        "Нажми значок с перечёркнутым окном справа в адресной строке, разреши всплывающие окна " +
        "для этой страницы и нажми кнопку ещё раз.</div>"
      : "";

    var body = state.view === "board" ? renderAdminBoard() : renderAdminQuestion();
    document.getElementById("root").innerHTML =
      '<div class="admin">' + head + warning + body + teamsHtml(false) + "</div>";
  }

  function renderAdminBoard() {
    return boardHtml(false) +
      '<p class="hint">Нажми на клетку, чтобы открыть вопрос. ' +
      "Ответы видны только здесь — на экране зала их нет.</p>";
  }

  function renderAdminQuestion() {
    var theme = PACK.themes[state.themeIndex];
    var question = currentQuestion();

    var picture = question.image
      ? '<div class="admin-picture"><img src="' + question.image + '" alt="">' +
        '<span class="hint">' +
        (question.imageWhen === "question" ? "Зал видит эту картинку с самого начала" : "Покажется залу вместе с ответом") +
        "</span></div>"
      : "";

    var answerBox =
      '<div class="answer-box"><div class="label">Правильный ответ</div>' +
      '<div class="text">' + escapeHtml(question.answer) + "</div>" +
      (question.comment ? '<div class="comment">' + escapeHtml(question.comment) + "</div>" : "") +
      "</div>";

    var transport =
      '<div class="transport">' +
      '<button class="primary big" data-action="play">' + (state.playing ? "⏸ Пауза" : "▶ Играть") + "</button>" +
      '<button data-action="back">−' + NUDGE + " с</button>" +
      '<button data-action="forward">+' + NUDGE + " с</button>" +
      '<button data-action="restart">⏮ С начала</button>' +
      '<span class="spacer"></span>' +
      '<button data-action="answer"' + (state.answerShown ? " disabled" : "") + ">Показать ответ</button>" +
      '<button data-action="close">Закрыть вопрос</button>' +
      "</div>";

    var judging = "";
    if (state.buzzed !== null) {
      judging =
        '<div class="transport"><strong>Отвечает: ' + escapeHtml(PACK.teams[state.buzzed]) + "</strong>" +
        '<button class="good big" data-judge="' + state.buzzed + ',1">Верно +' + question.price + "</button>" +
        '<button class="bad big" data-judge="' + state.buzzed + ',0">Неверно</button></div>';
    } else if (!state.answerShown) {
      judging = '<p class="hint">Ждём кнопку. Команда нажимает свою цифру, или нажми её сам: ' +
        PACK.teams.map(function (name, index) {
          return "<kbd>" + BUZZ_KEYS[index] + "</kbd> " + escapeHtml(name);
        }).join(" · ") + "</p>";
    }

    return (
      '<div class="question">' +
      '<div class="where">' + escapeHtml(theme.title) + " · " + question.price +
      (state.answerShown ? " · играет оригинал" : "") + "</div>" +
      answerBox + picture +
      seekHtml(true) +
      (state.audioProblem ? '<div class="hint">Звук не запустился, нажми «Играть» ещё раз</div>' : "") +
      transport + judging +
      '<p class="hint"><kbd>пробел</kbd> играть и пауза · <kbd>←</kbd> <kbd>→</kbd> перемотка на ' +
      NUDGE + " с · <kbd>1</kbd>…<kbd>9</kbd> кнопка команды · <kbd>Esc</kbd> закрыть вопрос</p>" +
      "</div>"
    );
  }

  function renderStage() {
    if (!stageAlive()) return;
    var main;

    if (state.view === "board") {
      main = boardHtml(true);
    } else {
      var theme = PACK.themes[state.themeIndex];
      var question = currentQuestion();
      var center;

      // Картинка «в вопросе» висит с самого начала и остаётся при ответе,
      // картинка «к ответу» появляется только когда ответ раскрыт.
      var early = question.image && question.imageWhen === "question";
      var picture = question.image && (early || state.answerShown)
        ? '<img class="stage-picture" src="' + question.image + '" alt="">'
        : "";

      if (state.answerShown) {
        center = picture + '<div class="answer">' + escapeHtml(question.answer) + "</div>" + seekHtml(false);
      } else if (state.buzzed !== null) {
        center = picture + '<div class="buzzed">' + escapeHtml(PACK.teams[state.buzzed]) + "</div>" +
          '<div class="status">отвечает</div>';
      } else {
        center = picture + '<div class="price">' + question.price + "</div>" + seekHtml(false) +
          '<div class="status">' + (state.playing ? "Слушаем…" : "Готовы") + "</div>";
      }

      main = '<div class="where">' + escapeHtml(theme.title) + "</div>" + center;
    }

    stage.document.getElementById("root").innerHTML =
      '<div class="stage"><div class="stage-main">' + main + "</div>" + teamsHtml(true) + "</div>";
  }

  function render() {
    renderAdmin();
    renderStage();
    paintPosition();
  }

  // ---------------------------------------------------------------- события

  /* Полосу ищем заново на каждое движение: если экран между делом
     перерисовался, старая ссылка указывала бы на выброшенный из документа
     элемент, и перемотка молча перестала бы работать. */
  function seekFromPointer(clientX) {
    var bar = document.querySelector("[data-seek]");
    if (!bar) return;
    var box = bar.getBoundingClientRect();
    if (box.width <= 0) return;
    seekTo(((clientX - box.left) / box.width) * trackLength());
  }

  document.addEventListener("pointerdown", function (event) {
    if (!event.target.closest("[data-seek]")) return;
    event.preventDefault();
    seekFromPointer(event.clientX);

    // Тянем головку, пока кнопка зажата, — как в обычном плеере.
    var move = function (moveEvent) { seekFromPointer(moveEvent.clientX); };
    var up = function () {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  });

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-open],[data-action],[data-judge],[data-score]");
    if (!target) return;

    if (target.dataset.open) {
      var open = target.dataset.open.split(",");
      openQuestion(Number(open[0]), Number(open[1]));
    } else if (target.dataset.judge) {
      var judgement = target.dataset.judge.split(",");
      judge(Number(judgement[0]), judgement[1] === "1");
    } else if (target.dataset.score) {
      var change = target.dataset.score.split(",");
      adjustScore(Number(change[0]), Number(change[1]));
    } else {
      ({
        stage: openStage,
        play: togglePlay,
        back: function () { nudge(-NUDGE); },
        forward: function () { nudge(NUDGE); },
        restart: restart,
        answer: showAnswer,
        close: closeQuestion,
        viz: toggleViz,
        reset: resetGame
      }[target.dataset.action] || function () {})();
    }
  });

  function onKey(event) {
    if (event.target && /^(INPUT|TEXTAREA)$/.test(event.target.tagName)) return;
    if (state.view !== "question") return;
    var key = event.key;

    if (key === " ") { event.preventDefault(); togglePlay(); return; }
    if (key === "ArrowLeft") { event.preventDefault(); nudge(-NUDGE); return; }
    if (key === "ArrowRight") { event.preventDefault(); nudge(NUDGE); return; }
    if (key === "Escape") { closeQuestion(); return; }

    var team = BUZZ_KEYS.indexOf(key);
    if (team >= 0 && team < PACK.teams.length) { event.preventDefault(); buzz(team); }
  }

  document.addEventListener("keydown", onKey);

  // Клавиши должны срабатывать, даже когда фокус в окне зала.
  var attachStageKeys = setInterval(function () {
    if (stageAlive() && !stage.__keysAttached) {
      stage.__keysAttached = true;
      stage.document.addEventListener("keydown", onKey);
    }
  }, 500);
  window.addEventListener("beforeunload", function () { clearInterval(attachStageKeys); });

  restore();
  render();
})();
