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
        render();
      }).catch(function () {
        state.playing = false;
        state.audioProblem = true;
        render();
      });
    };

    if (player.readyState >= 1) begin();
    else player.addEventListener("loadedmetadata", begin, { once: true });
  }

  function pause() {
    if (frame) cancelAnimationFrame(frame);
    frame = null;
    player.pause();
    state.playing = false;
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
      '<body><div id="root"></div></body></html>'
    );
    stage.document.close();
    stage.focus();
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
      answerBox +
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

      if (state.answerShown) {
        center = '<div class="answer">' + escapeHtml(question.answer) + "</div>" + seekHtml(false);
      } else if (state.buzzed !== null) {
        center = '<div class="buzzed">' + escapeHtml(PACK.teams[state.buzzed]) + "</div>" +
          '<div class="status">отвечает</div>';
      } else {
        center = '<div class="price">' + question.price + "</div>" + seekHtml(false) +
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
