// jsPsych timeline for the chain. The server (chain_server.py) decides every trial; this page
// shows the pair, times the click and sends back the side chosen.
//
// The whole chain runs inside ONE jsPsych trial that keeps its own DOM, so the question never
// re-renders: only the two option texts (and the progress) change. With every trial the server
// also sends the trial each side leads to ("next"), so the next pair is shown the moment the
// participant clicks and the answer is sent in the background.
// A reload restarts this timeline, and /chain/next resumes the session where it was.
// The last screen (thank you + completion code) is the end of the study: the jsPsych trial is
// never finished on purpose, so Potato never moves the participant on to its own pages.
window.potatoTimeline = function (jsPsych, potato) {
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
    '.chain-top{display:flex;justify-content:space-between;align-items:center;gap:1em;font-size:clamp(13px,0.95vw,20px);color:#bbb;margin-bottom:.5em}' +
    '.chain-home{background:none;border:0;padding:0 .5em 0 0;color:#bbb;cursor:pointer;vertical-align:middle;line-height:0}' +
    '.chain-home:hover{color:#888}' +
    '.chain-dialog.chain-info{max-width:min(90vw,900px);max-height:85vh;overflow:auto;text-align:left;line-height:1.6}' +
    '.chain-info h2{font-size:1.3em;margin:0 0 .8em}.chain-info h3{font-size:1.05em;margin:1.2em 0 .4em}' +
    '.chain-info a{color:#6b5ccf}.chain-info .consent-note{display:none}' +
    '.chain-info .chain-back{display:block;margin:1.4em auto 0}' +
    '.chain-hint{font-size:clamp(13px,0.95vw,20px);color:#888;margin:-1.6vw auto 2.4vw;max-width:75vw}' +
    '.chain-skip{display:block;margin:1.6vw auto 0;background:none;border:0;padding:.3em;font:inherit;' +
    'font-size:clamp(13px,0.95vw,20px);color:#bbb;text-decoration:underline;cursor:pointer}' +
    '.chain-skip:hover{color:#888}' +
    '.chain-modal{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:10}' +
    '.chain-modal[hidden]{display:none}' +
    '.chain-dialog{background:#fff;border-radius:8px;padding:1.6em 2em;max-width:min(90vw,520px);text-align:center;' +
    'font-size:clamp(16px,1.2vw,26px);box-shadow:0 6px 24px rgba(0,0,0,.2)}' +
    '.chain-dialog p{margin:0 0 1.2em}' +
    '.chain-dialog button{font:inherit;font-size:.95em;padding:.45em 1.4em;margin:0 .4em;border-radius:6px;cursor:pointer}' +
    '.chain-yes{background:#6b5ccf;color:#fff;border:0}.chain-no{background:#fff;color:#222;border:1px solid #bbb}' +
    '.chain-bar{height:max(6px,0.45vw);background:#e5e5e5;border-radius:99px;overflow:hidden;margin-bottom:3vw}' +
    '.chain-bar>div{height:100%;width:0;background:#6b5ccf;transition:width 200ms ease-out}' +
    '.chain-end{font-size:clamp(18px,1.5vw,32px);line-height:1.6;max-width:60vw;margin:0 auto}' +
    '.chain-end h2{font-size:1.3em;margin:0 0 1em}' +
    '.chain-code{display:inline-block;font:600 1.3em/1.4 ui-monospace,monospace;letter-spacing:.05em;' +
    'padding:.3em .9em;margin:.3em 0 1em;border:2px dashed #6b5ccf;border-radius:6px;user-select:all}' +
    '.chain-btn{display:inline-block;padding:.5em 1.4em;border-radius:6px;background:#6b5ccf;color:#fff;text-decoration:none}' +
    '.chain-note{font-size:.8em;color:#777}' +
    '.chain-fade{animation:chain-fade 120ms ease-out}' +
    '@keyframes chain-fade{from{opacity:0}to{opacity:1}}';
  document.head.appendChild(style);

  var chain = {
    type: jsPsychCallFunction,
    async: true,
    func: function (done) {
      var root = jsPsych.getDisplayElement();
      root.innerHTML =
        '<div class="chain"><div class="chain-top"><span><button type="button" class="chain-home" aria-label="Study information" ' +
          'title="Study information"><svg width=\'1.1em\' height=\'1.1em\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\' aria-hidden=\'true\'><path d=\'M3 11l9-8 9 8\'/><path d=\'M5 10v10h14V10\'/></svg></button><span class="chain-progress"></span></span>' +
        '<span>Questions? Message us on Prolific</span></div>' +
        '<div class="chain-bar" role="progressbar" aria-label="Progress"><div></div></div>' +
        '<p class="chain-q"></p>' +
        '<p class="chain-hint">There are no &ldquo;best&rdquo;, &ldquo;right&rdquo;, or &ldquo;wrong&rdquo; answers here, ' +
        'we are interested in your own, highly subjective opinion.</p><div class="chain-pair">' +
        '<button type="button" class="chain-opt" data-side="left"></button>' +
        '<button type="button" class="chain-opt" data-side="right"></button>' +
        '</div><button type="button" class="chain-skip">Feeling uncomfortable with these items?</button></div>' +
        '<div class="chain-modal" hidden><div class="chain-dialog" role="dialog" aria-modal="true">' +
        '<p>Would you like to skip this item?</p>' +
        '<button type="button" class="chain-yes">Yes</button><button type="button" class="chain-no">No</button>' +
        '</div></div>' +
        '<div class="chain-modal chain-infobox" hidden><div class="chain-dialog chain-info" role="dialog" aria-modal="true">' +
        '<div class="chain-infotext">Loading…</div>' +
        '<button type="button" class="chain-yes chain-back">Back to the task</button></div></div>';
      var question = root.querySelector('.chain-q');
      var pair = root.querySelector('.chain-pair');
      var bar = root.querySelector('.chain-bar');
      var progress = root.querySelector('.chain-progress');
      var skipLink = root.querySelector('.chain-skip');
      var modal = root.querySelector('.chain-modal');
      var infobox = root.querySelector('.chain-infobox');

      function setProgress(j) {
        var pct = nTrials ? 100 * j / nTrials : 0;
        bar.firstChild.style.width = pct + '%';
        bar.setAttribute('aria-valuenow', pct);
        progress.textContent = 'Progress ' + pct.toFixed(2) + '%';
      }
      var button = {left: root.querySelector('[data-side=left]'), right: root.querySelector('[data-side=right]')};

      var nTrials = 0;
      var shown = null;     // trial on screen: {i_trial, left, right}
      var nextFor = null;   // {left, right}: what each side leads to; null while the prefetch is on its way
      var shownAt = 0;      // performance.now() at the frame the pair appeared
      var queued = null;    // a click that came before the prefetch: [side, rt]
      var ended = false;

      function finish(reply) {          // the study's last screen
        ended = true;
        root.innerHTML = '<div class="chain-end"><h2>Thank you for your participation!</h2>' +
          (reply.completion_code
            ? '<p>Your Prolific completion code is:</p><div class="chain-code">' + esc(reply.completion_code) + '</div>'
            : '') +
          '<p>Your submission will be manually approved by the researcher, ' +
          'who will check it as soon as possible.</p>' +
          (reply.redirect_url   // Prolific participants: back to Prolific with the code
            ? '<p><a class="chain-btn" href="' + esc(reply.redirect_url) + '">Back to Prolific</a></p>' +
              '<p class="chain-note">You will be taken back to Prolific in a few seconds.</p>'
            : '') + '</div>';
        if (reply.redirect_url) setTimeout(function () { window.location.href = reply.redirect_url; }, 5000);
      }

      function fail(err) {
        ended = true;
        root.innerHTML = '<div class="chain-end"><p>Connection problem (' + esc(String(err)) + ').</p>' +
          '<p>Please reload this page; you will continue where you left off.</p></div>';
      }

      function show(t) {
        shown = t;
        button.left.textContent = t.left;
        button.right.textContent = t.right;
        setProgress(t.i_trial);
        pair.style.visibility = '';
        pair.classList.remove('chain-fade');
        void pair.offsetWidth;            // restart the fade
        pair.classList.add('chain-fade');
        // RT starts at the start of the frame that first paints the new texts (the fade's first frame)
        requestAnimationFrame(function () { shownAt = performance.now(); });
      }

      function sync(reply) {              // adopt the server's state as it is
        if (reply.done) { finish(reply); return; }
        nTrials = reply.n_trials;
        question.textContent = reply.question;
        show(reply.trial);
        nextFor = reply.next;
      }

      function answer(side, rt) {
        if (!nextFor) { queued = [side, rt]; return; }
        var seq = shown.seq;
        var next = nextFor[side];
        nextFor = null;
        if (next) { show(next); } else { pair.style.visibility = 'hidden'; setProgress(nTrials); }
        // a skipped pair keeps its i_trial, so trials are told apart by seq
        call('POST', '/chain/choose', {seq: seq, side: side, rt_ms: rt}).then(function (reply) {
          if (reply.stale || reply.done || !next || reply.trial.seq !== next.seq) { sync(reply); return; }
          nextFor = reply.next;
          if (queued) { var q = queued; queued = null; answer(q[0], q[1]); }
        }).catch(fail);
      }

      ['left', 'right'].forEach(function (side) {
        button[side].addEventListener('click', function () {
          if (!shown || ended || queued || !modal.hidden || !infobox.hidden) { return; }
          answer(side, Math.max(0, Math.round(performance.now() - shownAt)));
        });
      });

      // "Feeling uncomfortable with these items?" -> confirm -> the server puts a new pair on screen
      // time spent in a dialog is not response time: shownAt moves forward by it
      var pausedAt = null;
      function pause() { if (pausedAt === null) { pausedAt = performance.now(); } }
      function resume() { if (pausedAt !== null) { shownAt += performance.now() - pausedAt; pausedAt = null; } }

      skipLink.addEventListener('click', function () {
        if (!shown || ended || queued) { return; }
        pause();
        modal.hidden = false;
        modal.querySelector('.chain-no').focus();
      });
      modal.querySelector('.chain-no').addEventListener('click', function () { modal.hidden = true; resume(); });
      modal.querySelector('.chain-yes').addEventListener('click', function () {
        modal.hidden = true;
        resume();
        if (!shown || ended || queued) { return; }
        answer('skip', Math.max(0, Math.round(performance.now() - shownAt)));
      });

      // the "home" icon: the consent page's text (study information) over the task, then back
      var infoLoaded = false;
      root.querySelector('.chain-home').addEventListener('click', function () {
        if (ended) { return; }
        pause();
        infobox.hidden = false;
        infobox.querySelector('.chain-back').focus();
        if (!infoLoaded) {
          call('GET', '/chain/consent_text').then(function (r) {
            infobox.querySelector('.chain-infotext').innerHTML = r.html;   // researcher-written HTML from consent.jsonl
            infoLoaded = true;
          }).catch(function () {
            infobox.querySelector('.chain-infotext').textContent = 'The study information could not be loaded.';
          });
        }
      });
      infobox.querySelector('.chain-back').addEventListener('click', function () { infobox.hidden = true; resume(); });

      call('GET', '/chain/next').then(sync).catch(fail);
    }
  };

  return [chain];
};
