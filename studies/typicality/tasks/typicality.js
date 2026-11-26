// jsPsych timeline for the typicality ratings. The server (typ_server.py) fixes the order and
// sends every item text at the start, so moving to the next item never waits for the network:
// each rating is sent in the background, in order.
//
// The whole study runs inside ONE jsPsych trial that keeps its own DOM: the question stays,
// only the stimulus text, the slider and the progress bar change.
// A reload restarts this timeline, and /typ/start resumes the session where it was.
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
      // 409 = stale answer (double click, second tab): the body says where the session is
      if (r.ok || r.status === 409) { return r.json(); }
      throw new Error('HTTP ' + r.status);
    });
  }

  // Sizes follow the window width so the task fills the screen at the browser's default zoom.
  var style = document.createElement('style');
  style.textContent =
    'html,body{margin:0}' +
    '#runtime-target{height:100vh}' +
    '.typ{width:min(80vw,1400px);margin:0 auto}' +
    '.typ-q{font-size:clamp(20px,1.7vw,36px);font-weight:600;line-height:1.35;margin:0 auto 2.4vw}' +
    '.typ-stim{font-size:clamp(18px,1.5vw,32px);line-height:1.5;color:#222;text-align:left;' +
    'padding:1.6vw 1.8vw;min-height:6vw;border:1px solid #bbb;border-radius:6px;background:#fff}' +
    // slider: thin light track, round black thumb, the value floating above the thumb; shorter
    // than the page so it does not read as a progress bar
    '.typ-scale{width:min(60vw,1000px);margin:3.2vw auto 0;position:relative;padding-top:2.4em;' +
    'font-size:clamp(16px,1.2vw,26px)}' +
    '.typ-range{-webkit-appearance:none;appearance:none;width:100%;margin:0;height:24px;background:none;cursor:pointer}' +
    '.typ-range:focus{outline:none}' +
    '.typ-range::-webkit-slider-runnable-track{height:6px;background:#eee;border:1px solid #ccc;border-radius:99px}' +
    '.typ-range::-moz-range-track{height:6px;background:#eee;border:1px solid #ccc;border-radius:99px}' +
    '.typ-range::-webkit-slider-thumb{-webkit-appearance:none;width:20px;height:20px;margin-top:-8px;' +
    'border-radius:50%;background:#000;border:0}' +
    '.typ-range::-moz-range-thumb{width:20px;height:20px;border-radius:50%;background:#000;border:0}' +
    '.typ-value{position:absolute;top:0;transform:translateX(-50%);font-size:1.4em;line-height:1;color:#000}' +
    '.typ-labels{display:flex;justify-content:space-between;color:#555;margin-top:1.4em}' +
    '.typ-next{font:inherit;font-size:clamp(16px,1.2vw,26px);margin-top:2.2vw;padding:.5em 1.4em;' +
    'border:0;border-radius:6px;background:#6b5ccf;color:#fff;cursor:pointer}' +
    '.typ-next:disabled{background:#c9c4e8;cursor:default}' +
    '.typ-top{display:flex;justify-content:space-between;align-items:center;gap:1em;font-size:clamp(13px,0.95vw,20px);color:#bbb;margin-bottom:.5em}' +
    '.typ-home{background:none;border:0;padding:0 .5em 0 0;color:#bbb;cursor:pointer;vertical-align:middle;line-height:0}' +
    '.typ-home:hover{color:#888}' +
    '.typ-dialog.typ-info{max-width:min(90vw,900px);max-height:85vh;overflow:auto;text-align:left;line-height:1.6}' +
    '.typ-info h2{font-size:1.3em;margin:0 0 .8em}.typ-info h3{font-size:1.05em;margin:1.2em 0 .4em}' +
    '.typ-info a{color:#6b5ccf}.typ-info .consent-note{display:none}' +
    '.typ-info .typ-back{display:block;margin:1.4em auto 0}' +
    '.typ-hint{font-size:clamp(13px,0.95vw,20px);color:#888;margin:-1.6vw auto 2.4vw}' +
    '.typ-skip{display:block;margin:.8vw auto 0;background:none;border:0;padding:.3em;font:inherit;' +
    'font-size:clamp(13px,0.95vw,20px);color:#bbb;text-decoration:underline;cursor:pointer}' +
    '.typ-skip:hover{color:#888}' +
    '.typ-modal{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:10}' +
    '.typ-modal[hidden]{display:none}' +
    '.typ-dialog{background:#fff;border-radius:8px;padding:1.6em 2em;max-width:min(90vw,520px);text-align:center;' +
    'font-size:clamp(16px,1.2vw,26px);box-shadow:0 6px 24px rgba(0,0,0,.2)}' +
    '.typ-dialog p{margin:0 0 1.2em}' +
    '.typ-dialog button{font:inherit;font-size:.95em;padding:.45em 1.4em;margin:0 .4em;border-radius:6px;cursor:pointer}' +
    '.typ-yes{background:#6b5ccf;color:#fff;border:0}.typ-no{background:#fff;color:#222;border:1px solid #bbb}' +
    '.typ-bar{height:max(6px,0.45vw);background:#e5e5e5;border-radius:99px;overflow:hidden;margin-bottom:3vw}' +
    '.typ-bar>div{height:100%;width:0;background:#6b5ccf;transition:width 200ms ease-out}' +
    '.typ-fade{animation:typ-fade 120ms ease-out}@keyframes typ-fade{from{opacity:0}to{opacity:1}}' +
    '.typ-end{font-size:clamp(18px,1.5vw,32px);line-height:1.6;max-width:60vw;margin:0 auto}' +
    '.typ-end h2{font-size:1.3em;margin:0 0 1em}' +
    '.typ-code{display:inline-block;font:600 1.3em/1.4 ui-monospace,monospace;letter-spacing:.05em;' +
    'padding:.3em .9em;margin:.3em 0 1em;border:2px dashed #6b5ccf;border-radius:6px;user-select:all}' +
    '.typ-btn{display:inline-block;padding:.5em 1.4em;border-radius:6px;background:#6b5ccf;color:#fff;text-decoration:none}' +
    '.typ-note{font-size:.8em;color:#777}';
  document.head.appendChild(style);

  var task = {
    type: jsPsychCallFunction,
    async: true,
    func: function (done) {
      var root = jsPsych.getDisplayElement();

      function finish(reply) {          // the study's last screen
        root.innerHTML = '<div class="typ-end"><h2>Thank you for your participation!</h2>' +
          (reply.completion_code
            ? '<p>Your Prolific completion code is:</p><div class="typ-code">' + esc(reply.completion_code) + '</div>'
            : '') +
          '<p>Your submission will be manually approved by the researcher, ' +
          'who will check it as soon as possible.</p>' +
          (reply.redirect_url   // Prolific participants: back to Prolific with the code
            ? '<p><a class="typ-btn" href="' + esc(reply.redirect_url) + '">Back to Prolific</a></p>' +
              '<p class="typ-note">You will be taken back to Prolific in a few seconds.</p>'
            : '') + '</div>';
        if (reply.redirect_url) setTimeout(function () { window.location.href = reply.redirect_url; }, 5000);
      }

      function fail(err) {
        root.innerHTML = '<div class="typ-end"><p>Connection problem (' + esc(String(err)) + ').</p>' +
          '<p>Please reload this page; you will continue where you left off.</p></div>';
      }

      call('GET', '/typ/start').then(function (st) {
        if (st.done) { finish(st); return; }
        var decimals = (String(st.step).split('.')[1] || '').length;
        root.innerHTML =
          '<div class="typ"><div class="typ-top"><span><button type="button" class="typ-home" aria-label="Study information" ' +
          'title="Study information"><svg width=\'1.1em\' height=\'1.1em\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\' aria-hidden=\'true\'><path d=\'M3 11l9-8 9 8\'/><path d=\'M5 10v10h14V10\'/></svg></button><span class="typ-progress"></span></span>' +
          '<span>Questions? Message us on Prolific</span></div>' +
          '<div class="typ-bar" role="progressbar" aria-label="Progress"><div></div></div>' +
          '<p class="typ-q"></p>' +
          '<p class="typ-hint">There are no &ldquo;best&rdquo;, &ldquo;right&rdquo;, or &ldquo;wrong&rdquo; answers here, ' +
          'we are interested in your own, highly subjective opinion.</p><div class="typ-stim"></div>' +
          '<button type="button" class="typ-skip">Feeling uncomfortable with this item?</button>' +
          '<div class="typ-scale"><span class="typ-value"></span>' +
          '<input type="range" class="typ-range" aria-label="Rating">' +
          '<div class="typ-labels"><span></span><span></span></div></div>' +
          '<button type="button" class="typ-next" disabled>Submit</button></div>' +
          '<div class="typ-modal" hidden><div class="typ-dialog" role="dialog" aria-modal="true">' +
          '<p>Would you like to skip this item?</p>' +
          '<button type="button" class="typ-yes">Yes</button><button type="button" class="typ-no">No</button>' +
          '</div></div>' +
          '<div class="typ-modal typ-infobox" hidden><div class="typ-dialog typ-info" role="dialog" aria-modal="true">' +
          '<div class="typ-infotext">Loading…</div>' +
          '<button type="button" class="typ-yes typ-back">Back to the task</button></div></div>';
        var stim = root.querySelector('.typ-stim');
        var range = root.querySelector('.typ-range');
        var labels = root.querySelectorAll('.typ-labels span');
        var progress = root.querySelector('.typ-progress');
        var shown = root.querySelector('.typ-value');
        var next = root.querySelector('.typ-next');
        var bar = root.querySelector('.typ-bar');
        var skipLink = root.querySelector('.typ-skip');
        var modal = root.querySelector('.typ-modal');
        var infobox = root.querySelector('.typ-infobox');
        root.querySelector('.typ-q').textContent = st.question;
        labels[0].textContent = st.left_label;
        labels[1].textContent = st.right_label;
        range.min = st.min_value;
        range.max = st.max_value;
        range.step = st.step;

        var items = st.items, n = items.length, i = st.i_trial;
        var shownAt = 0, firstTouch = null, ended = false;
        var queue = Promise.resolve();   // ratings go out one after another, in order

        function snap(v) {              // onto the step grid, without float noise
          var k = Math.round((v - st.min_value) / st.step);
          return Number((st.min_value + k * st.step).toFixed(decimals));
        }

        function setProgress(j) {
          var pct = 100 * j / n;
          bar.firstChild.style.width = pct + '%';
          bar.setAttribute('aria-valuenow', pct);
          progress.textContent = 'Progress ' + pct.toFixed(2) + '%';
        }

        function placeValue() {         // keep the number above the thumb (20px wide)
          var f = (parseFloat(range.value) - st.min_value) / (st.max_value - st.min_value);
          shown.style.left = 'calc(' + (f * 100) + '% + ' + (10 - 20 * f) + 'px)';
        }

        function showValue() {
          shown.textContent = snap(parseFloat(range.value)).toFixed(decimals);
          placeValue();
        }

        function show(j) {
          i = j;
          stim.textContent = items[j];
          stim.style.visibility = skipLink.style.visibility = '';
          // thumb and value visible from the start, at the middle; Submit waits for a touch
          range.value = snap((st.min_value + st.max_value) / 2);
          showValue();
          next.disabled = true;
          firstTouch = null;
          setProgress(j);
          stim.classList.remove('typ-fade');
          void stim.offsetWidth;          // restart the fade
          stim.classList.add('typ-fade');
          // RT starts at the start of the frame that first paints the new stimulus
          requestAnimationFrame(function () { shownAt = performance.now(); });
        }

        function touched() {
          if (ended) { return; }
          if (firstTouch === null) { firstTouch = Math.max(0, Math.round(performance.now() - shownAt)); }
          showValue();
          next.disabled = false;
        }
        range.addEventListener('input', touched);
        range.addEventListener('pointerdown', function () { setTimeout(touched, 0); });

        function handle(reply) {
          if (reply.done) { ended = true; finish(reply); return; }
          if (reply.stale) { show(reply.i_trial); }  // the server is elsewhere: follow it
        }

        function send(body) {           // move on at once; the answer goes out in the background, in order
          if (i + 1 < n) { show(i + 1); } else {
            setProgress(n);
            root.querySelector('.typ-scale').style.visibility = 'hidden';
            stim.style.visibility = skipLink.style.visibility = 'hidden';
            next.disabled = true;
          }
          queue = queue.then(function () { return call('POST', '/typ/rate', body); })
            .then(handle)
            .catch(function (e) { ended = true; fail(e); });
        }

        next.addEventListener('click', function () {
          if (next.disabled || ended || !modal.hidden || !infobox.hidden) { return; }
          send({i_trial: i, value: snap(parseFloat(range.value)),
                rt_ms: Math.max(0, Math.round(performance.now() - shownAt)), first_touch_ms: firstTouch});
        });

        // "Feeling uncomfortable with this item?" -> confirm -> skip it (not rated)
        // time spent in a dialog is not response time: shownAt moves forward by it
        var pausedAt = null;
        function pause() { if (pausedAt === null) { pausedAt = performance.now(); } }
        function resume() { if (pausedAt !== null) { shownAt += performance.now() - pausedAt; pausedAt = null; } }

        skipLink.addEventListener('click', function () {
          if (ended || stim.style.visibility === 'hidden') { return; }
          pause();
          modal.hidden = false;
          modal.querySelector('.typ-no').focus();
        });
        modal.querySelector('.typ-no').addEventListener('click', function () { modal.hidden = true; resume(); });
        modal.querySelector('.typ-yes').addEventListener('click', function () {
          modal.hidden = true;
          resume();
          if (ended || stim.style.visibility === 'hidden') { return; }
          send({i_trial: i, skip: true, rt_ms: Math.max(0, Math.round(performance.now() - shownAt))});
        });

        // the "home" icon: the consent page's text (study information) over the task, then back
        var infoLoaded = false;
        root.querySelector('.typ-home').addEventListener('click', function () {
          if (ended) { return; }
          pause();
          infobox.hidden = false;
          infobox.querySelector('.typ-back').focus();
          if (!infoLoaded) {
            call('GET', '/typ/consent_text').then(function (r) {
              infobox.querySelector('.typ-infotext').innerHTML = r.html;   // researcher-written HTML from consent.jsonl
              infoLoaded = true;
            }).catch(function () {
              infobox.querySelector('.typ-infotext').textContent = 'The study information could not be loaded.';
            });
          }
        });
        infobox.querySelector('.typ-back').addEventListener('click', function () { infobox.hidden = true; resume(); });

        show(i);
      }).catch(fail);
      // done() is never called: the last screen ends the study (see the top of this file)
    }
  };

  return [task];
};
