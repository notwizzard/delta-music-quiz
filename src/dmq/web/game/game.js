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

  // ---------------------------------------------------------------- состояние

  var state = {
    view: "board",
    themeIndex: 0,
    questionIndex: 0,
    used: {},          // "тема:вопрос" -> true
    scores: PACK.teams.map(function () { return 0; }),
    revealIndex: 0,
    playing: false,
    position: 0,       // сколько секунд уже прозвучало
    buzzed: null,      // индекс команды, нажавшей кнопку
    audioProblem: false,
    stageBlocked: false,
    spent: {},         // команды, уже ответившие неверно на этом вопросе
    answerShown: false
  };

  // ------------------------------------------------------------------- звук

  var blobUrls = {};
  var player = new Audio();
  var currentUrl = null;
  var limit = 0;
  var frame = null;

  function audioUrl(key, base64) {
    if (blobUrls[key]) return blobUrls[key];
    var binary = atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    blobUrls[key] = URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" }));
    return blobUrls[key];
  }

  /* Останавливаться нужно точно на границе шага, а событие timeupdate приходит
     всего несколько раз в секунду — этого мало, поэтому следим через rAF. */
  function watch() {
    if (!state.playing) return;
    state.position = player.currentTime;
    if (limit && player.currentTime >= limit) {
      player.pause();
      state.playing = false;
      state.position = limit;
      render();
      return;
    }
    paintProgress();
    frame = requestAnimationFrame(watch);
  }

  /* Отрисовка никогда не ждёт звук. Если бы интерфейс обновлялся только после
     успешного player.play(), то любая заминка с загрузкой оставляла бы ведущего
     перед экраном, который врёт: шаг уже переключился, а на экране прежний. */
  function play(url, from, to) {
    stop();
    limit = to;
    state.position = from;
    state.audioProblem = false;

    // Следующий кусок того же файла — это просто перемотка, а не новая загрузка.
    if (currentUrl !== url) {
      currentUrl = url;
      player.src = url;
    }

    var begin = function () {
      try { player.currentTime = from; } catch (error) { /* до загрузки перемотка недоступна */ }
      player.play().then(function () {
        state.playing = true;
        frame = requestAnimationFrame(watch);
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

  function stop() {
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

  function currentSteps() {
    var question = currentQuestion();
    return question ? question.reveal : [];
  }

  function openedUntil() {
    var steps = currentSteps();
    return steps.length ? steps[Math.min(state.revealIndex, steps.length - 1)] : 0;
  }

  function totalLength() {
    var steps = currentSteps();
    return steps.length ? steps[steps.length - 1] : 0;
  }

  function isLastStep() {
    return state.revealIndex >= currentSteps().length - 1;
  }

  // --------------------------------------------------------------- действия

  function openQuestion(themeIndex, questionIndex) {
    if (state.used[themeIndex + ":" + questionIndex]) return;
    stop();
    state.view = "question";
    state.themeIndex = themeIndex;
    state.questionIndex = questionIndex;
    state.revealIndex = 0;
    state.position = 0;
    state.buzzed = null;
    state.spent = {};
    state.answerShown = false;
    render();
  }

  function playCurrent(fromStart) {
    var question = currentQuestion();
    if (!question) return;
    var url = audioUrl(question.audioKey, question.audio);
    play(url, fromStart ? 0 : state.position, openedUntil());
  }

  /* Главная механика: открыть следующий кусок и доиграть именно его,
     продолжив с того места, где остановились. */
  function revealMore() {
    if (isLastStep()) return;
    var previous = openedUntil();
    state.revealIndex += 1;
    render();
    var question = currentQuestion();
    play(audioUrl(question.audioKey, question.audio), previous, openedUntil());
  }

  function togglePlay() {
    if (state.playing) {
      stop();
      render();
      return;
    }
    // Дослушали до границы — следующее нажатие играет открытый кусок заново.
    playCurrent(state.position >= openedUntil() - 0.02);
    render();
  }

  function buzz(teamIndex) {
    if (state.view !== "question" || state.answerShown) return;
    if (state.buzzed !== null || state.spent[teamIndex]) return;
    stop();
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
    stop();
    state.answerShown = true;
    render();
    var question = currentQuestion();
    if (question && question.answerAudio) {
      play(audioUrl(question.answerAudioKey, question.answerAudio), 0, question.answerDuration || 0);
    }
  }

  function closeQuestion() {
    stop();
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
    stop();
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

  function revealHtml() {
    var steps = currentSteps();
    var previous = 0;
    return '<div class="reveal">' + steps.map(function (step, index) {
      var span = step - previous;
      var opened = index <= state.revealIndex;
      var html =
        '<div class="step' + (opened ? " open" : "") + '" style="--grow:' + Math.max(span, 0.4) + '" data-step="' + index + '">' +
        '<div class="fill" data-fill="' + index + '"></div>' +
        '<div class="num">' + formatSeconds(step) + "</div></div>";
      previous = step;
      return html;
    }).join("") + "</div>";
  }

  function formatSeconds(value) {
    return (value < 10 ? value.toFixed(1) : Math.round(value)) + " с";
  }

  /* Заливку двигаем напрямую по стилю, без перерисовки всего экрана —
     иначе на каждом кадре пересобирался бы весь DOM обоих окон. */
  function paintProgress() {
    // После раскрытия играет уже ответ, и шкала вопроса к нему отношения не имеет.
    if (state.answerShown) return;
    var steps = currentSteps();
    var previous = 0;
    for (var index = 0; index < steps.length; index++) {
      var span = steps[index] - previous;
      var ratio = span > 0 ? (state.position - previous) / span : 0;
      var percent = Math.max(0, Math.min(1, ratio)) * 100 + "%";
      var nodes = [document.querySelector('[data-fill="' + index + '"]')];
      if (stageAlive()) nodes.push(stage.document.querySelector('[data-fill="' + index + '"]'));
      for (var n = 0; n < nodes.length; n++) if (nodes[n]) nodes[n].style.width = percent;
      previous = steps[index];
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
    var last = isLastStep();

    var answerBox =
      '<div class="answer-box"><div class="label">Правильный ответ</div>' +
      '<div class="text">' + escapeHtml(question.answer) + "</div>" +
      (question.comment ? '<div class="comment">' + escapeHtml(question.comment) + "</div>" : "") +
      "</div>";

    var transport =
      '<div class="transport">' +
      '<button class="primary big" data-action="play">' + (state.playing ? "⏸ Пауза" : "▶ Играть") + "</button>" +
      '<button class="big" data-action="more"' + (last ? " disabled" : "") + ">＋ Ещё кусочек</button>" +
      '<button data-action="restart">⏮ С начала</button>' +
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
      '<div class="where">' + escapeHtml(theme.title) + " · " + question.price + "</div>" +
      answerBox +
      revealHtml() +
      '<div class="hint">Открыто ' + formatSeconds(openedUntil()) + " из " + formatSeconds(totalLength()) +
      " · шаг " + (state.revealIndex + 1) + " из " + currentSteps().length +
      (state.audioProblem ? " · звук не запустился, нажми «Играть» ещё раз" : "") + "</div>" +
      transport + judging +
      '<p class="hint"><kbd>пробел</kbd> играть и пауза · <kbd>→</kbd> ещё кусочек · ' +
      "<kbd>1</kbd>…<kbd>9</kbd> кнопка команды · <kbd>Esc</kbd> закрыть вопрос</p>" +
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
        center = '<div class="answer">' + escapeHtml(question.answer) + "</div>";
      } else if (state.buzzed !== null) {
        center = '<div class="buzzed">' + escapeHtml(PACK.teams[state.buzzed]) + "</div>" +
          '<div class="status">отвечает</div>';
      } else {
        center = '<div class="price">' + question.price + "</div>" + revealHtml() +
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
    paintProgress();
  }

  // ---------------------------------------------------------------- события

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
        more: revealMore,
        restart: function () { playCurrent(true); },
        answer: showAnswer,
        close: closeQuestion,
        reset: resetGame
      }[target.dataset.action] || function () {})();
    }
  });

  function onKey(event) {
    if (event.target && /^(INPUT|TEXTAREA)$/.test(event.target.tagName)) return;
    var key = event.key;

    if (state.view === "question") {
      if (key === " ") { event.preventDefault(); togglePlay(); return; }
      if (key === "ArrowRight") { event.preventDefault(); revealMore(); return; }
      if (key === "Escape") { closeQuestion(); return; }
      var team = BUZZ_KEYS.indexOf(key);
      if (team >= 0 && team < PACK.teams.length) { event.preventDefault(); buzz(team); }
    }
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
