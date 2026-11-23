# forced-choice-app

Potato-based app for forced-choice judgement experiments: a participant walks one
MCMC-with-People style chain (Sanborn & Griffiths, 2008). Each draw shows the current
reference $x$ and a proposal $x^* = \texttt{propose\_fn}(x)$ side by side in random
order, the participant picks $\hat{x}$, and $x \leftarrow \hat{x}$.

Potato provides the study shell (URL login, the jsPsych page, a closing page, admin).
The chain itself is served by `chain_server.py`, which holds all chain state on the
server: the browser only sends the side chosen and the response time.

## Run

```bash
conda activate lingu                       # potato-annotation 2.14.0, networkx, flask
python run.py                              # reads sessions.yaml; serves on 127.0.0.1:8100
```

Do not use `potato start`: it would not add the `/chain/*` routes.

- **The participant's view**: the question stays on screen, the two options swap in place after each
  click, a progress bar runs underneath. The last screen thanks them, shows the spec's
  `completion_code` and says the submission will be approved manually; that screen is the end
  of the study (Potato's own annotation page is never reached).
- **Participant URL** (one URL = one session):
  `http://127.0.0.1:8100/?sid=<user_id>--spec=<spec>--chain=<k>`,
  e.g. `?sid=u1--spec=dummy_envy--chain=1`. `user_id` uses letters, digits, `_` and single `-`.
  Same URL again resumes the session (also after a server restart); a new `chain` number is a new chain.
- **Monitoring**: `http://127.0.0.1:8100/chain/admin?key=<admin key>`. The key is in
  `admin_api_key.txt`, which Potato writes on the first request to `/admin`.
- **Export**: `python export.py` writes `export--trials.csv` and `export--sessions.csv`
  into the study's data folder (rewritten each run).
- **Tests**: `python -m unittest discover -s tests -v`

## Files

| Path | Purpose |
|---|---|
| `sessions.yaml` | Study variables (`study_id`, paths, host, port) and the session **specs** |
| `config.yaml` | Potato config: URL login on `sid`, the `chain` jsPsych phase (+ an unreachable placeholder annotation phase Potato requires) |
| `run.py` | Starts Potato (`create_app`) with the chain blueprint |
| `chain_core.py` | Specs, asset loading/checks, session id, one session's trials (no Flask) |
| `chain_server.py` | `/chain/next`, `/chain/choose`, `/chain/admin`, storage |
| `tasks/chain.js` | jsPsych timeline: shows the server's trials, posts the choices |
| `proposal/graph/base.py` | `GraphProposal`, the base class for graph proposals |
| `proposal/graph/uniform_neighbor.py` | $x^*$ uniform over the neighbours of $x$ |
| `proposal/parametric/` | Reserved for future proposals |
| `export.py` | Merge all sessions into CSVs |
| `data/closing.jsonl` | Placeholder item for the required annotation phase |
| `assets/stimuli/<stimuli_name>/` | `stimuli.csv`, `<graph_name>.json` (networkx node-link), `attention_checks.csv` |

## A spec

```yaml
specs:
  dummy_envy:
    stimuli_name: dummy_emosent_binary     # assets/stimuli/<stimuli_name>/
    graph_name: b=4--emb=spades            # <graph_name>.json in that folder
    propose_fn: graph/uniform_neighbor     # proposal/graph/uniform_neighbor.py
    propose_params: {}                     # keyword arguments of that class
    n_draw: 20
    question: Pick the sentence that ...
    n_attention: 3                         # attention item k goes after draw floor(k*n_draw/(K+1))
    attention_pass: 3
    completion_code: DUMMY123              # shown on the last screen; empty = none
```

Everything is loaded and checked at boot, so a bad spec stops the server before anyone arrives.

## Adding a proposal

Add `proposal/graph/<name>.py` with exactly one subclass of `GraphProposal` that
implements `propose(self, x, rng)` (all randomness from `rng`, no per-session state) and
optionally `check(self)` for boot-time warnings. Then use `propose_fn: graph/<name>`.

## Data

```
responses_data/<study_id>/<user_id>/spec=<spec>--chain=<k>/
  session.json    spec, seed, asset sha256, status, running summary
  trials.jsonl    one line per answered trial
```

Trial fields: `i_trial, trial_type (chain|attention), i_draw, n_draw, x_id, x_star_id,
x_hat_id, left_id, chosen_side, att_id, correct, rt_ms, server_ms, t_prev, t_answered,
user_id, sid`. Stimuli are stored as ids; texts are in `stimuli.csv`.
`rt_ms` is measured in the browser (`performance.now()`) from the frame the pair appears to
the click. The page shows the next pair as soon as the participant clicks (the server sends,
with each trial, the trial each side leads to), so the server never sees a trial appear;
`server_ms` is the server-side gap between this answer and the previous one (or the page
load), a cross-check for `rt_ms` that also contains network jitter.

The seed is a hash of `(study_id, user_id, spec, chain)`, so a session is reproducible
from its URL and its choices.
