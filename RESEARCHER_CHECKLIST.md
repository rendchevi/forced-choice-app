# 🧭 Researcher checklist: from a stimulus folder to two Prolific URLs

All paths are relative to `~/rnd/app/forced-choice-app`. Tick the boxes as you go.

---

## 1. 📁 Check the stimulus folder

`assets/stimuli/<stimuli_name>/` must contain:

| File | Used by | Columns / format |
|---|---|---|
| `stimuli.csv` | both | `stimulus_id, stimulus, ref_label` |
| `<graph_name>.json` | chain | networkx node-link graph, **b-regular** (every node has the same number of neighbours) |
| `attention_checks_choice.csv` | chain | `att_id, stimulus_A, stimulus_B, answer` (`A` = left, `B` = right) |
| `attention_checks_typicality.csv` | typicality | `att_id, stimulus, answer` (`min` or `max`) |

- [ ] All four files are there.
- [ ] Every attention check's text matches its answer (left/right, min/max).

> 💡 If a session needs more attention checks than the file has, items are reused evenly (never twice in a row) and the server log shows a warning.

## 2. 🧾 Draft the studies on Prolific (do not publish yet)

- [ ] One **typicality** study, and one **chain** study **per spec**.
- [ ] For each study, create the **completion code** (action: *Manually review*) and note it.
- [ ] Typicality only: also create a **no-consent code** (action: e.g. *Request return*). The chain consent page has a single checkbox, so a participant who does not consent simply returns the study.
- [ ] Choose **"I'll use URL parameters"** (the URLs are in step 8).

## 3. ✍️ Consent page

| Study | File | What to do |
|---|---|---|
| Chain | `studies/chain/surveyflow/consent.jsonl` | Done (approved text). Edit the `"description"` of the first line to change the text (HTML allowed). |
| Chain | `studies/chain/surveyflow/information_sheet.pdf` | The full Information Sheet & Consent Form. Replace the file to update it; no restart needed. |
| Typicality | `studies/typicality/surveyflow/consent.jsonl` | ⚠️ Still a **placeholder**: replace the text before running typicality. |

The task pages show the same text again behind the 🏠 icon, read from these files.

## 4. ⚙️ Chain config

- [ ] Copy `studies/chain/sessions.yaml` to a new file, e.g. `studies/chain/sessions--goemotion_pilot.yaml`.
- [ ] Edit:

| Key | Meaning |
|---|---|
| `study_id` | New name, e.g. `goemotion_pilot--v1`. The data goes to `responses_data/<study_id>/`. |
| `specs:` → `<spec name>` | The spec name goes into the Prolific URL (`&spec=<spec name>`). Letters, digits, `_` only. |
| `stimuli_name` | The folder name from step 1. |
| `graph_name` | The graph file name without `.json`. |
| `propose_params` | `{p_geom, p_jump, n_stuck}` (`n_stuck: null` = never). |
| `n_draw`, `n_chain` | Draws per chain, chains per session. |
| `n_attention`, `attention_pass` | Attention checks per chain, correct answers needed per chain. |
| `question` | The question shown above the two texts. |
| `completion_code` | The code from step 2. |

## 5. ⚙️ Typicality config

- [ ] Copy `studies/typicality/study.yaml` to e.g. `studies/typicality/study--goemotion_pilot.yaml`.
- [ ] Edit `study_id`, `stimuli_name`, `n_attention`, `question`, `min_value` / `max_value` / `step`, `left_label` / `right_label`, `completion_code`, `no_consent_code`.

## 6. 🚀 Start everything

Paste this into a terminal on the workstation (use your own config file names):

```bash
cd ~/rnd/app/forced-choice-app
PY=~/miniconda3/envs/lingu/bin/python
tmux kill-session -t forced-choice 2>/dev/null
tmux new-session -d -s forced-choice -n chain -c "$PWD/studies/chain" "$PY run.py sessions--goemotion_pilot.yaml 2>&1 | tee -a server.log"
tmux new-window -t forced-choice -n typicality -c "$PWD/studies/typicality" "$PY run.py study--goemotion_pilot.yaml 2>&1 | tee -a server.log"
tmux new-window -t forced-choice -n tunnel "cloudflared tunnel --config ~/.cloudflared/forced-choice.yml run"
```

| To… | Do |
|---|---|
| see the logs | `tmux attach -t forced-choice` |
| switch window | `Ctrl-b n` |
| leave without stopping | `Ctrl-b d` |
| stop everything | `tmux kill-session -t forced-choice` |

> ⚠️ If a config is wrong, that server stops and prints the error in its window.

## 7. 🧪 Test it yourself (also on a phone)

| What | URL |
|---|---|
| Chain test link | `https://chain.reneren.xyz/?sid=test1--spec=<spec name>` |
| Typicality test link | `https://typicality.reneren.xyz/?sid=test1--run=1` |
| Fake Prolific link (chain) | `https://chain.reneren.xyz/?PROLIFIC_PID=aaaaaaaaaaaaaaaaaaaaaaaa&STUDY_ID=bbbbbbbbbbbbbbbbbbbbbbbb&SESSION_ID=111111111111111111111111&spec=<spec name>` |
| Chain admin | `https://chain.reneren.xyz/chain/admin?key=<contents of studies/chain/admin_api_key.txt>` |
| Typicality admin | `https://typicality.reneren.xyz/typ/admin?key=<contents of studies/typicality/admin_api_key.txt>` |

- [ ] Consent → task → final screen works in both studies.
- [ ] The 🏠 icon, "Feeling uncomfortable…" (skip) and the final redirect work.
- [ ] A new user name (`test2`, `test3`, …) starts a fresh session; the same name resumes.

> 💡 Test sessions land in the running `study_id`. Use a temporary `study_id` for testing, or test user names starting with `test` (the admin page has a search box).

## 8. 🔗 The two URLs for Prolific

**Typicality**

```
https://typicality.reneren.xyz/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}
```

**Chain** (one Prolific study per spec)

```
https://chain.reneren.xyz/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}&spec=<spec name>
```

## 9. 👀 While the study runs, and after

- [ ] Keep the workstation and the `forced-choice` tmux session running.
- [ ] Do not change a config or stimulus file that a running study uses.
- [ ] Watch the admin pages (filter `fail` / `abandoned`), then approve or reject on Prolific.
- [ ] When done, export in each study folder:

```bash
cd studies/chain && $PY export.py sessions--goemotion_pilot.yaml
cd ../typicality && $PY export.py study--goemotion_pilot.yaml
```

The CSVs (`export--trials.csv`, `export--sessions.csv`, `export--skips.csv`) are written to `responses_data/<study_id>/`.

- [ ] Data handling promised in the consent form (done by hand): delete unfinished and withdrawn sessions, and replace Prolific IDs with internal codes once payment is complete.
