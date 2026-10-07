"""Append reviewer-written, rerun-confirmed failure→rule lessons to stage skills."""

from pathlib import Path


HERE = Path(__file__).resolve().parent


def targets(comparison, repair_types=()):
    if comparison.get("policy_changed"):
        return []  # A human-approved policy change is not a successful repair lesson.
    improved = []
    for stage in (1, 3, 4, 5):
        before = comparison["same_retained_tasks"]["before"]["stage_counts"][str(stage)].get("passed", 0)
        after = comparison["same_retained_tasks"]["after"]["stage_counts"][str(stage)].get("passed", 0)
        if after > before:
            improved.append(stage)
    if improved and "infrastructure" in repair_types:
        improved.append("infrastructure")
    return improved


def append_confirmed(comparison, lessons, *, dataset, wave, iteration,
                     evidence, repair_record, repair_types=(), skill_root=None):
    wanted = targets(comparison, repair_types)
    if not wanted:
        return []
    if not isinstance(lessons, list) or len(lessons) != len(wanted):
        raise ValueError(f"reviewer must provide one lesson for each improved target: {wanted}")
    indexed = {}
    required = ("failure_signature", "cause", "general_rule", "match_predicate", "limits")
    for lesson in lessons:
        if not isinstance(lesson, dict) or lesson.get("stage") not in wanted:
            raise ValueError("lesson has an unexpected or invalid stage")
        stage = lesson["stage"]
        if stage in indexed or any(not isinstance(lesson.get(key), str) or not lesson[key].strip()
                                   for key in required):
            raise ValueError("lesson is duplicate or missing required fields")
        indexed[stage] = lesson
    if set(indexed) != set(wanted):
        raise ValueError("reviewer lessons do not cover every improved target")
    root = Path(skill_root) if skill_root else HERE / "skills"
    written = []
    effect = comparison.get("full_source_effect", {})
    for stage in wanted:
        lesson = indexed[stage]
        path = root / ("infrastructure" if stage == "infrastructure" else f"stage-{stage}") / "SKILL.md"
        if not path.is_file():
            raise ValueError(f"missing skill file: {path}")
        lines = [f"### {dataset} — wave {wave}, repair {iteration}", ""]
        for key, label in (("failure_signature", "Failure signature"), ("cause", "Cause"),
                           ("general_rule", "General rule"), ("match_predicate", "Match predicate"),
                           ("limits", "Limits and counterexamples")):
            value = " ".join(lesson[key].split())[:1200]
            lines.append(f"- **{label}:** {value}")
        if stage != "infrastructure":
            before = comparison["same_retained_tasks"]["before"]["stage_counts"][str(stage)].get("passed", 0)
            after = comparison["same_retained_tasks"]["after"]["stage_counts"][str(stage)].get("passed", 0)
            lines.append(f"- **Same retained pilot tasks:** stage {stage} passed {before} → {after}.")
        lines.append(f"- **Full-source effect:** {len(effect.get('changed_ids', []))} changed, "
                     f"{len(effect.get('removed_ids', []))} removed, "
                     f"{len(effect.get('added_ids', []))} added task packages.")
        lines.append(f"- **Evidence:** `{evidence}`; repair record: `{repair_record}`.")
        # A resumed controller may re-run an outcome review before advancing
        # its loop-state checkpoint. Keep each confirmed lesson once.
        if f"- **Evidence:** `{evidence}`;" in path.read_text():
            written.append(str(path))
            continue
        with path.open("a") as stream:
            stream.write("\n" + "\n".join(lines) + "\n")
        written.append(str(path))
    return written
