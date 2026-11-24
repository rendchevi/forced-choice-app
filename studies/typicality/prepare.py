"""Build this study's Potato inputs from study.yaml and the shared assets.

    python prepare.py [study.yaml]

Potato refuses data files outside the study folder (even through a symlink), so the shared
stimuli are copied in. Everything written here is derived and rewritten on every run:

    data/stimuli.csv      assets/stimuli/<stimuli_name>/stimuli.csv (checked) + the attention items as rows
    data/attention.json   attention_checks_typicality.json in Potato's format, for grading

Attention items are ordinary rows of the data file, so every participant rates every one of them,
at random positions like the stimuli. Potato's own injection (attention_checks.frequency) is not
used: it adds each injected item to the shared pool, where random assignment hands it to later
participants as a regular item, so the number of checks per participant drifted between 2 and 6.
    config.yaml           the Potato config
"""

import csv
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SCHEME = "typicality"


def js_number(v) -> str:
    """How the browser writes a range input's value, which is what Potato compares attention answers to."""
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def check_stimuli(path: Path) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ids = [r["stimulus_id"] for r in rows]
    texts = [r["stimulus"].strip() for r in rows]
    if not rows or not all(texts):
        raise ValueError(f"{path}: empty file or empty stimulus")
    if len(set(ids)) != len(ids) or len(set(texts)) != len(texts):
        raise ValueError(f"{path}: duplicate stimulus_id or stimulus text")
    return rows


def attention_items(path: Path, study: dict) -> list:
    """Scale-neutral items ({att_id, stimulus, answer: min|max}) -> Potato attention items."""
    items = json.loads(path.read_text(encoding="utf-8"))
    ends = {"min": js_number(study["min_value"]), "max": js_number(study["max_value"])}
    out = []
    for it in items:
        if it.get("answer") not in ends or not it.get("stimulus", "").strip():
            raise ValueError(f"{path}: bad item {it}")
        # shown like a stimulus: the instruction sits where the stimulus text goes, the question stays the same
        out.append({"id": it["att_id"], "stimulus_id": it["att_id"], "stimulus": it["stimulus"],
                    "expected_answer": {SCHEME: ends[it["answer"]]}})
    if len({o["id"] for o in out}) != len(out):
        raise ValueError(f"{path}: duplicate att_id")
    return out


def potato_config(study: dict, n_items: int) -> dict:
    return {
        "annotation_task_name": "Typicality judgements",
        "task_dir": ".",
        "output_annotation_dir": f"annotation_output/{study['study_id']}/",
        "data_files": ["data/stimuli.csv"],
        "item_properties": {"id_key": "stimulus_id", "text_key": "stimulus"},
        "assignment_strategy": "random",
        "max_annotations_per_user": n_items,  # every stimulus and every attention item
        # one URL = one session: ?sid=<user_id>--chain=<k> (checked in run.py)
        "login": {"type": "url_direct", "url_argument": "sid"},
        "user_config": {"allow_all_users": True},
        "annotation_schemes": [{
            "annotation_type": "vas", "name": SCHEME, "description": study["question"],
            "min_value": study["min_value"], "max_value": study["max_value"],
            "left_label": study["left_label"], "right_label": study["right_label"],
            "label_requirement": {"required": True},
        }],
        # no frequency/probability: nothing is injected, the items file only grades (see top)
        "attention_checks": {"enabled": True, "items_file": "data/attention.json"},
        "completion_code": study["completion_code"],
    }


def main(study_path="study.yaml", out: Path = ROOT) -> dict:
    """Write data/ and config.yaml into ``out`` (the study folder; tests pass a temp folder)."""
    study = yaml.safe_load((ROOT / study_path).read_text(encoding="utf-8"))
    if not study["min_value"] < study["max_value"]:
        raise ValueError("need min_value < max_value")
    src = (ROOT / study["assets_root"] / study["stimuli_name"]).resolve()
    data = Path(out) / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows = check_stimuli(src / "stimuli.csv")
    att = attention_items(src / "attention_checks_typicality.json", study)
    if {a["id"] for a in att} & {r["stimulus_id"] for r in rows}:
        raise ValueError("an att_id is also a stimulus_id")
    with open(data / "stimuli.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
        w.writerows({**{k: "" for k in rows[0]}, "stimulus_id": a["id"], "stimulus": a["stimulus"]} for a in att)
    (data / "attention.json").write_text(json.dumps(att, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(rows)
    config = potato_config(study, n + len(att))
    header = "# GENERATED by prepare.py from study.yaml; edit study.yaml instead.\n"
    (Path(out) / "config.yaml").write_text(header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"prepared {study['study_id']}: {n} stimuli, {len(att)} attention items, config.yaml")
    return study


if __name__ == "__main__":
    main(*sys.argv[1:])
