// jsPsych timeline for the chain. The server (chain_server.py) decides every trial; this page
// shows the pair, times the click and sends back the side chosen.
//
// The whole chain runs inside ONE jsPsych trial that keeps its own DOM, so the question never
// re-renders: only the two option texts (and the progress) change. With every trial the server
// also sends the trial each side leads to ("next"), so the next pair is shown the moment the
// participant clicks and the answer is sent in the background.
// A reload restarts this timeline, and /chain/next resumes the session where it was.
window.potatoTimeline = function (jsPsych, potato) {
  var failed = null;   // set when the server could not be reached

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function call(method, url, body) {
    return fetch(url, {
      method: method,
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      // 409 = stale answer (double click, second tab): the body carries the current state
      if (r.ok || r.status === 409) { return r.json(); }
      throw new Error('HTTP ' + r.status);
    });
  }

  // Sizes follow the window width so the task fills the screen at the browser's default zoom.
  var style = document.createElement('style');
  style.textContent =
    'html,body{margin:0}' +
    '#runtime-target{height:100vh}' +
    '.chain{width:min(92vw,1800px);margin:0 auto}' +
    '.chain-q{font-size:clamp(20px,1.7vw,36px);font-weight:600;line-height:1.35;max-width:75vw;margin:0 auto 2.4vw}' +
    '.chain-pair{display:grid;grid-template-columns:1fr 1fr;gap:2vw}' +
    '.chain-opt{font:inherit;font-size:clamp(18px,1.5vw,32px);line-height:1.5;color:#222;text-align:left;' +
    'white-space:normal;padding:1.6vw 1.8vw;min-height:9vw;background:#fff;border:1px solid #bbb;' +
    'border-radius:6px;cursor:pointer}' +
    '.chain-opt:hover{border-color:#666;background:#fafafa}' +
    '.chain-opt:focus{outline:none}.chain-opt:focus-visible{outline:3px solid #93c5fd}' +
    '.chain-progress{color:#888;font-size:clamp(13px,1vw,20px);margin-top:2.4vw}' +
    '.chain-fade{animation:chain-fade 120ms ease-out}' +
    '@keyframes chain-fade{from{opacity:0}to{opacity:1}}';
  document.head.appendChild(style);

  var chain = {
    type: jsPsychCallFunction,
    async: true,
    func: function (done) {
      var root = jsPsych.getDisplayElement();
      root.innerHTML =
        '<div class="chain"><p class="chain-q"></p><div class="chain-pair">' +
        '<button type="button" class="chain-opt" data-side="left"></button>' +
        '<button type="button" class="chain-opt" data-side="right"></button>' +
        '</div><p class="chain-progress"></p></div>';
      var question = root.querySelector('.chain-q');
      var pair = root.querySelector('.chain-pair');
      var progress = root.querySelector('.chain-progress');
      var button = {left: root.querySelector('[data-side=left]'), right: root.querySelector('[data-side=right]')};

      var nTrials = 0;
      var shown = null;     // trial on screen: {i_trial, left, right}
      var nextFor = null;   // {left, right}: what each side leads to; null while the prefetch is on its way
      var shownAt = 0;      // performance.now() at the frame the pair appeared
      var queued = null;    // a click that came before the prefetch: [side, rt]
      var ended = false;

      function finish(err) {
        if (ended) { return; }
        ended = true;
        if (err) { failed = String(err); }
        done(err ? {chain_error: failed} : {});
      }

      function show(t) {
        shown = t;
        button.left.textContent = t.left;
        button.right.textContent = t.right;
        progress.textContent = (t.i_trial + 1) + ' / ' + nTrials;
        pair.style.visibility = '';
        pair.classList.remove('chain-fade');
        void pair.offsetWidth;            // restart the fade
        pair.classList.add('chain-fade');
        // RT starts at the start of the frame that first paints the new texts (the fade's first frame)
        requestAnimationFrame(function () { shownAt = performance.now(); });
      }

      function sync(reply) {              // adopt the server's state as it is
        nTrials = reply.n_trials;
        question.textContent = reply.question;
        if (reply.done) { finish(); return; }
        show(reply.trial);
        nextFor = reply.next;
      }

      function answer(side, rt) {
        if (!nextFor) { queued = [side, rt]; return; }
        var i = shown.i_trial;
        var next = nextFor[side];
        nextFor = null;
        if (next) { show(next); } else { pair.style.visibility = 'hidden'; }
        call('POST', '/chain/choose', {i_trial: i, side: side, rt_ms: rt}).then(function (reply) {
          if (reply.stale || reply.done || !next || reply.trial.i_trial !== next.i_trial) { sync(reply); return; }
          nextFor = reply.next;
          if (queued) { var q = queued; queued = null; answer(q[0], q[1]); }
        }).catch(finish);
      }

      ['left', 'right'].forEach(function (side) {
        button[side].addEventListener('click', function () {
          if (!shown || ended || queued) { return; }
          answer(side, Math.max(0, Math.round(performance.now() - shownAt)));
        });
      });

      call('GET', '/chain/next').then(sync).catch(finish);
    }
  };

  var end = {
    type: jsPsychHtmlButtonResponse,
    stimulus: function () {
      return failed
        ? '<p>Connection problem (' + esc(failed) + '). Please reload this page; you will continue where you left off.</p>'
        : '<p>All choices are done. Thank you!</p>';
    },
    choices: function () { return failed ? [] : ['Continue']; }
  };

  return [chain, end];
};
