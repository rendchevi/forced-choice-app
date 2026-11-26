# forced-choice-app

Potato-based apps for the human studies of this project. The same participants (same
`user_id`) take part in both, so the data can be joined per individual.

| Study | Folder | Task | Port |
|---|---|---|---|
| Forced-choice chain | `studies/chain/` | MCMC-with-People chain of 2AFC choices (custom server + jsPsych) | 8100 |
| Typicality | `studies/typicality/` | rate every stimulus on a continuous scale (custom server + jsPsych) | 8200 |

```
assets/stimuli/<stimuli_name>/       shared by both studies
  stimuli.csv                        stimulus_id, stimulus, ref_label
  <graph_name>.json                  networkx node-link graphs (chain proposals)
  attention_checks_choice.csv        chain: att_id, stimulus_A, stimulus_B, answer (A = left)
  attention_checks_typicality.csv    typicality: att_id, stimulus, answer (min|max)
studies/chain/                       one folder per study, self-contained
studies/typicality/
```

Each study runs as its own server (`conda activate lingu` first: potato-annotation 2.14.0,
waitress) and uses its own cookie name, so both can be open in one browser. Hosting for Prolific
(Cloudflare tunnel from this workstation): `deploy/README.md`.

**Entry.** Prolific participants arrive with
`?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}` (chain: plus
`&spec=<spec>`, one Prolific study per spec). The server checks the three ids (24 lowercase hex),
gives each new Prolific `SESSION_ID` the user's next unused chain number $k$, and redirects to
the session's `?sid=...` URL; the same `SESSION_ID` always returns to the same session. The ids
are stored in `responses_data/<study_id>/<PROLIFIC_PID>/prolific.json` and in each
`session.json` (`prolific`). Test links with `?sid=...` work as before; opening a URL with a
different `sid` logs the previous session out.

**Consent.** Each study starts with Potato's consent page (`surveyflow/consent.jsonl`, styled by
`surveyflow/consent.css` like the task pages; Potato's theme is forced light). Chain: the approved
summary text, one required checkbox, and a link to the full Information Sheet & Consent Form,
served from `surveyflow/information_sheet.pdf` at `/information_sheet.pdf` (replace that file to
update it; no restart needed). Someone who does not consent closes the page and returns the study
on Prolific. Typicality: placeholder text with "I agree / I do not agree"; declining ends the
study on `templates/done.html`, which sends Prolific participants back with `no_consent_code`.

**On the task pages.** "Questions? Message us on Prolific" sits right of "Progress N%"; under
the question a small note says there are no best, right or wrong answers. Under the items,
"Feeling uncomfortable with these items?" opens "Would you like to skip this item?" (Yes/No).
A skip is not an answer: chain — the chain stays at $x$ and gets a new proposal (at a chain's
first draw it also gets a new starting stimulus), the draw is repeated; typicality — the item
is not rated. Skips are listed in `session.json` (`skips`) and `export--skips.csv`, not in
`trials.jsonl`. Skipping an attention check counts as a wrong answer (recorded as a trial).

**End.** The last screen shows the completion code; Prolific participants are sent back to
`https://app.prolific.com/submissions/complete?cc=<code>` after 5 s (or with the button).

**Monitoring** (`/chain/admin`, `/typ/admin`, `?key=<admin_api_key.txt>`): one row per session
with state (`running`, `abandoned` = no answer for `abandon_after_min`, `pass`, `fail`), Prolific
study and session ids, duration, median RT; filter by state or Prolific study, search by PID or
sid, optional 30 s auto-refresh, CSV of the shown rows. Approve and reject on Prolific.

**Load test.** `python loadtest.py --n 300 --think 1.0 --ramp 10` in a study folder runs N
simulated Prolific participants (entry, consent, every trial) against a throwaway copy of the
study and reports latency per request type, errors and data completeness.

## Forced-choice chain (`studies/chain/`)

A participant walks one MCMC-with-People style chain (Sanborn & Griffiths, 2008). Each draw
shows the current reference $x$ and a proposal $x^* = \texttt{propose\_fn}(x)$ side by side
in random order, the participant picks $\hat{x}$, and $x \leftarrow \hat{x}$.
Potato provides the study shell (URL login, the jsPsych page, admin). The chain itself is
served by `chain_server.py`, which holds all chain state on the server: the browser only
sends the side chosen and the response time.

### Run

```bash
conda activate lingu                       # potato-annotation 2.14.0, networkx, flask
cd studies/chain && python run.py          # reads sessions.yaml; serves on 127.0.0.1:8100
```

Do not use `potato start`: it would not add the `/chain/*` routes.

- **The participant's view**: the question stays on screen, the two options swap in place after each
  click, a progress bar runs underneath. The last screen thanks them, shows the spec's
  `completion_code` and says the submission will be approved manually; that screen is the end
  of the study (Potato's own annotation page is never reached).
- **Participant URL** (one URL = one session):
  `http://127.0.0.1:8100/?sid=<user_id>--spec=<spec>`,
  e.g. `?sid=u1--spec=dummy_envy`. `user_id` uses letters, digits, `_` and single `-`.
  Same URL again resumes the session (also after a server restart); one session per user and spec.
  A session runs `n_chain` chains of `n_draw` draws one after the other; each chain starts at its own
  stimulus (drawn without replacement) and the participant sees no break between chains.
- **Monitoring**: `http://127.0.0.1:8100/chain/admin?key=<admin key>`. The key is in
  `admin_api_key.txt`, which Potato writes on the first request to `/admin`.
- **Export**: `python export.py` writes `export--trials.csv` and `export--sessions.csv`
  into the study's data folder (rewritten each run).
- **Tests**: `python -m unittest discover -s tests -v`

### Files

| Path | Purpose |
|---|---|
| `sessions.yaml` | Study variables (`study_id`, paths, host, port) and the session **specs** |
| `config.yaml` | Potato config: URL login on `sid`, the `chain` jsPsych phase (+ an unreachable placeholder annotation phase Potato requires) |
| `run.py` | Starts Potato (`create_app`) with the chain blueprint, served by waitress |
| `prolific.py` | Prolific ids → session id, completion URL |
| `surveyflow/` | Consent page and its CSS |
| `surveyflow/information_sheet.pdf` | Full Information Sheet & Consent Form linked from the consent page |
| `loadtest.py` | Simulated participants against a throwaway copy |
| `chain_core.py` | Specs, asset loading/checks, session id, one session's trials (no Flask) |
| `chain_server.py` | `/chain/next`, `/chain/choose`, `/chain/admin`, storage |
| `tasks/chain.js` | jsPsych timeline: shows the server's trials, posts the choices |
| `proposal/graph/base.py` | `GraphProposal`, the base class for graph proposals |
| `proposal/graph/dmcmcp.py` | d-MCMCP proposal (Hsu et al., 2019): random walk + uniform jump + stuck rule |
| `proposal/parametric/` | Reserved for future proposals |
| `export.py` | Merge all sessions into CSVs |
| `data/closing.jsonl` | Placeholder item for the required annotation phase |

### A spec

```yaml
specs:
  dummy_envy:
    stimuli_name: dummy_emosent_binary     # assets/stimuli/<stimuli_name>/
    graph_name: b=4--emb=spades            # <graph_name>.json in that folder
    propose_fn: graph/dmcmcp               # proposal/graph/dmcmcp.py
    propose_params: {p_geom: 0.0, p_jump: 0.1, n_stuck: null}   # keyword arguments of that class
    n_draw: 20                             # draws per chain
    n_chain: 2                             # chains per session, one after the other
    question: Pick the sentence that ...
    n_attention: 3                         # per chain; K = n_attention*n_chain checks, check k after global draw floor(k*n_draw*n_chain/(K+1))
    attention_pass: 3                      # per chain; the session needs attention_pass*n_chain correct
    completion_code: DUMMY123              # shown on the last screen; empty = none
```

Everything is loaded and checked at boot, so a bad spec stops the server before anyone arrives.

**Too few attention items** (both studies): if a session needs $K$ checks but the file has
$M<K$ items, items are reused evenly: rounds of all $M$ items in random order (the last round
cut short), so each item appears at most $\lceil K/M\rceil$ times and never twice in a row.
The boot log warns when this happens; `att_id` in the data shows the reuse.

### The proposal (`graph/dmcmcp`)

Discrete MCMCP (Hsu et al., 2019) on a $b$-regular stimulus graph (built elsewhere from a
similarity $s(x, x^*)$ and degree $b$). For reference $x$ and $n_\text{rejected}$ proposals
rejected in a row (participant kept $x$; attention trials do not count):

1. **stuck**: if $n_\text{stuck}$ is set and $n_\text{rejected}$ is a positive multiple of
   $n_\text{stuck}$, $x^* \sim \mathcal{U}(\mathcal{X}\setminus\{x\})$. The proposal then depends
   on the chain's history, so convergence to the target is no longer guaranteed. `null` = never.
2. **jump**: otherwise, with probability $p_\text{jump}$, $x^* \sim \mathcal{U}(\mathcal{X}\setminus\{x\})$.
3. **walk**: otherwise a random walk of $1+K$ steps, $P(K=k)=(1-p_\text{geom})\,p_\text{geom}^{k}$,
   each to a uniformly chosen neighbour; $x^*$ = the end node, redrawn if it is $x$.
   $p_\text{geom}=0$ is the uniform-neighbour proposal; expected length $1/(1-p_\text{geom})$.

Each chain trial stores `proposal_kind` (walk/jump/stuck), `n_steps` and `n_rejected`. The
boot log warns if the graph is not regular or `n_stuck` is set.

Adding another proposal: `proposal/graph/<name>.py` with exactly one subclass of
`GraphProposal` implementing `propose(self, x, rng, n_rejected) -> (x_star, info)` (all
randomness from `rng`, no per-session state), then `propose_fn: graph/<name>`.

### Data

```
responses_data/<study_id>/<user_id>/spec=<spec>/
  session.json    spec, seed, asset sha256, status, running summary
  trials.jsonl    one line per answered trial
```

Trial fields: `i_trial, trial_type (chain|attention), chain_idx, i_draw, n_draw, n_chain, x_id, x_star_id,
x_hat_id, left_id, chosen_side, proposal_kind, n_steps, n_rejected, att_id, correct, rt_ms, server_ms, t_prev, t_answered,
user_id, sid`. Stimuli are stored as ids; texts are in `stimuli.csv`.
`rt_ms` is measured in the browser (`performance.now()`) from the frame the pair appears to
the click. The page shows the next pair as soon as the participant clicks (the server sends,
with each trial, the trial each side leads to), so the server never sees a trial appear;
`server_ms` is the server-side gap between this answer and the previous one (or the page
load), a cross-check for `rt_ms` that also contains network jitter.

The seed is a hash of `(study_id, user_id, spec)`, so a session is reproducible
from its URL and its choices.

## Typicality (`studies/typicality/`)

Every participant rates every stimulus of `stimuli.csv` on a continuous scale
$[\text{min\_value}, \text{max\_value}]$, one stimulus at a time. Built like the chain: Potato
is the shell (URL login, the jsPsych page), `typ_server.py` serves the items and stores the
ratings, and `tasks/typicality.js` runs the whole study in one jsPsych trial with a fixed page.

```bash
cd studies/typicality && python run.py        # reads study.yaml; serves on 127.0.0.1:8200
```

- **The participant's view**: progress bar ("Progress N%") → question → stimulus → slider
  (thumb and number shown from the start, at the middle of the scale) → **Submit** (enabled
  once the slider was touched; `first_touch_ms` records when), centred. No navigation back or between items. All item texts are
  sent when the page opens, so the next item appears at once and each rating is sent in the
  background. The last screen thanks them, shows `completion_code` and says the submission will
  be approved manually.
- **Participant URL**: `http://127.0.0.1:8200/?sid=<user_id>--run=<k>`, `k` = repetition
  (same `user_id` as in the chain study). Same URL again resumes (also after a server restart).
- **Variables**: `study.yaml` (`study_id`, `stimuli_name`, `n_attention`, `question`, `min_value`, `max_value`,
  `step`, `left_label`, `right_label`, `completion_code`, paths, host, port).
- **Order and attention checks** (`order.py`): stimuli shuffled per session (seeded by study
  and `sid`, so reproducible); check $k$ of $K$ (`n_attention`, picked from `attention_checks_typicality.csv`) comes
  after stimulus $\lfloor kn/(K+1) \rfloor$. The instruction sits in the stimulus slot ("move the
  slider all the way to the RIGHT end") under the usual question; the server grades it
  (`answer: min|max` = exactly that end of the scale). Nobody is warned or stopped: the
  researcher decides at manual approval.
- **Monitoring**: `http://127.0.0.1:8200/typ/admin?key=<admin key>` (`admin_api_key.txt`,
  written on the first request to `/admin`): one row per session with checks right/total,
  median RT, filter and CSV download.
- **Data**:
  ```
  responses_data/<study_id>/<user_id>/run=<k>/
    session.json    scale settings, asset sha256, status, running summary
    trials.jsonl    one line per rating
  ```
  Fields: `i_trial, n_trials, trial_type (stimulus|attention), stimulus_id, att_id, value,
  correct, rt_ms, first_touch_ms, server_ms, t_answered, user_id, sid`. `rt_ms`: from the
  frame the stimulus appears to the click on Next (browser `performance.now()`);
  `first_touch_ms`: to the first touch of the slider; `server_ms`: server-side gap since the
  previous rating (or page load). `python export.py` merges all sessions into
  `export--trials.csv` and `export--sessions.csv`.
- **Tests**: `python -m unittest discover -s tests -v`
