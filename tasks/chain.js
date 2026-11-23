// jsPsych timeline for the chain. The server (chain_server.py) decides every trial;
// this page only shows the pair, and sends back the side chosen and the response time.
// A reload restarts this timeline, and /chain/next resumes the session where it was.
window.potatoTimeline = function (jsPsych, potato) {
  var cur = null;      // last server reply: {done, i_trial, n_trials, question, left, right}
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
      // 409 = stale answer (double click or second tab): the body carries the current trial
      if (r.ok || r.status === 409) { return r.json(); }
      throw new Error('HTTP ' + r.status);
    });
  }

  function step(request) {
    return function (done) {
      request().then(function (t) {
        cur = t;
        done({chain_i_trial: t.done ? null : t.i_trial});
      }).catch(function (e) {
        failed = String(e);
        cur = {done: true};
        done({chain_error: failed});
      });
    };
  }

  // Page style, added once. #runtime-target gets the full viewport height so jsPsych's
  // own margin:auto centring puts the trial in the middle of the screen.
  var style = document.createElement('style');
  style.textContent =
    'html,body{margin:0}' +
    '#runtime-target{height:100vh}' +
    '.chain-q{font-size:20px;font-weight:600;max-width:900px;margin:0 auto 24px}' +
    '.chain-opt{white-space:normal;text-align:left;font-size:18px;line-height:1.5;width:100%;min-height:120px;' +
    'padding:16px 20px;vertical-align:top}' +
    '#jspsych-html-button-response-btngroup{max-width:1100px;margin:0 auto;gap:24px}' +
    '.chain-progress{color:#888;font-size:13px;margin-top:20px}';
  document.head.appendChild(style);

  var first = {type: jsPsychCallFunction, async: true, func: step(function () { return call('GET', '/chain/next'); })};

  var choice = {
    type: jsPsychHtmlButtonResponse,
    stimulus: function () { return '<p class="chain-q">' + esc(cur.question) + '</p>'; },
    choices: function () { return [cur.left, cur.right]; },
    button_html: function (text) { return '<button class="jspsych-btn chain-opt">' + esc(text) + '</button>'; },
    button_layout: 'grid',
    grid_rows: 1,
    grid_columns: 2,
    prompt: function () { return '<p class="chain-progress">' + (cur.i_trial + 1) + ' / ' + cur.n_trials + '</p>'; },
    on_finish: function (data) {
      data.chain_i_trial = cur.i_trial;
      data.side = data.response === 0 ? 'left' : 'right';
    }
  };

  var send = {
    type: jsPsychCallFunction,
    async: true,
    func: function (done) {
      var last = jsPsych.data.getLastTrialData().values()[0];
      step(function () {
        return call('POST', '/chain/choose', {i_trial: last.chain_i_trial, side: last.side, rt_ms: last.rt});
      })(done);
    }
  };

  var loop = {
    timeline: [choice, send],
    conditional_function: function () { return !cur.done; },
    loop_function: function () { return !cur.done; }
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

  return [first, loop, end];
};
