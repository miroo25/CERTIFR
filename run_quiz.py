import json
import random
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

SYLLABUS_FILE = Path("syllabus.json")
QUESTIONS_FILE = Path("questions.json")
RESULTS_FILE = Path("quiz_results.json")

STATUS_BASE = {
    "not_started": 0.0,
    "in_progress": 0.5,
    "completed": 1.0,
}


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"Missing required file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calculate_topic_readiness(topic: dict) -> float:
    status = topic.get("status", "not_started")
    base_study = STATUS_BASE.get(status, 0.0) * 100.0
    confidence_pct = (max(0.0, min(5.0, float(topic.get("confidence", 0)))) / 5.0) * 100.0
    study_component = (0.6 * base_study) + (0.4 * confidence_pct)

    q_total = max(0, int(topic.get("questions_total", 0)))
    q_attempted = max(0, int(topic.get("questions_attempted", 0)))
    q_coverage_pct = min(100.0, (q_attempted / q_total) * 100.0) if q_total > 0 else None
    score_pct = max(0.0, min(100.0, float(topic.get("practice_score_pct", 0.0))))

    if q_coverage_pct is not None:
        readiness = (0.40 * study_component) + (0.25 * q_coverage_pct) + (0.35 * score_pct)
    else:
        readiness = (0.55 * study_component) + (0.45 * score_pct)
    return round(readiness, 1)


def list_topics(syllabus: dict) -> list[dict]:
    items = []
    for mod in syllabus.get("modules", []):
        for t in mod.get("topics", []):
            items.append({
                "id": t.get("id"),
                "title": t.get("title"),
                "readiness": calculate_topic_readiness(t),
                "q_attempted": t.get("questions_attempted", 0),
                "q_total": t.get("questions_total", 0),
                "score_pct": t.get("practice_score_pct", 0.0),
            })
    return items


def select_questions(questions: list[dict], syllabus: dict, topic_id: str | None, limit: int | None, shuffle: bool) -> list[dict]:
    pool = questions
    if topic_id and topic_id.upper() != "ALL":
        target = topic_id.strip().upper()
        pool = [q for q in questions if str(q.get("topic_id", "")).upper() == target]
    elif topic_id and topic_id.upper() == "WEAK":
        topics_sorted = sorted(list_topics(syllabus), key=lambda x: x["readiness"])
        weak_ids = [t["id"] for t in topics_sorted[:5]]
        pool = [q for q in questions if q.get("topic_id") in weak_ids]

    if shuffle:
        pool = list(pool)
        random.shuffle(pool)

    if limit is not None and limit > 0:
        pool = pool[:limit]

    return pool


def sync_syllabus_metrics(syllabus_path: Path, questions_data: dict, results_data: dict) -> None:
    syllabus = load_json(syllabus_path)
    questions = questions_data.get("questions", [])
    attempts = results_data.get("attempts", [])

    question_meta: dict[str, dict] = {}
    totals_by_topic: dict[str, int] = defaultdict(int)

    for q in questions:
        qid = q.get("id")
        tid = q.get("topic_id")
        if qid and tid:
            question_meta[qid] = {
                "topic_id": tid,
                "correct_answer": q.get("correct_answer"),
            }
            totals_by_topic[tid] += 1

    latest_by_qid: dict[str, dict] = {}
    for att in attempts:
        qid = att.get("question_id")
        if qid:
            latest_by_qid[qid] = att

    attempted_by_topic: dict[str, int] = defaultdict(int)
    correct_by_topic: dict[str, int] = defaultdict(int)

    for qid, att in latest_by_qid.items():
        meta = question_meta.get(qid)
        if not meta:
            continue
        tid = meta["topic_id"]
        attempted_by_topic[tid] += 1

        is_right = att["is_correct"] if isinstance(att.get("is_correct"), bool) else (
            att.get("selected_answer") == meta.get("correct_answer")
        )
        if is_right:
            correct_by_topic[tid] += 1

    for mod in syllabus.get("modules", []):
        for topic in mod.get("topics", []):
            tid = topic.get("id")
            if not tid:
                continue
            q_total = totals_by_topic.get(tid, 0)
            q_attempted = attempted_by_topic.get(tid, 0)
            q_correct = correct_by_topic.get(tid, 0)
            score_pct = round((q_correct / q_attempted) * 100.0, 1) if q_attempted > 0 else 0.0

            topic["questions_total"] = q_total
            topic["questions_attempted"] = q_attempted
            topic["practice_score_pct"] = score_pct
            if q_attempted > 0:
                topic["last_reviewed"] = date.today().isoformat()
                if topic.get("status") == "not_started":
                    topic["status"] = "in_progress"

    syllabus["last_updated"] = date.today().isoformat()
    save_json(syllabus, syllabus_path)


def prompt_filter(syllabus: dict) -> tuple[str | None, int | None, bool]:
    topics = list_topics(syllabus)
    print("\n=== Available Standards (topic_id) ===")
    for t in topics:
        print(
            f"  {t['id']:<14} | Readiness: {t['readiness']:5.1f}% | "
            f"Q: {t['q_attempted']}/{t['q_total']} | {t['title']}"
        )

    print("\nFilter options:")
    print("  - Enter a topic_id (e.g., TOP-IFRS15, TOP-IAS36, TOP-IAS12)")
    print("  - Enter 'WEAK' to practice top 5 lowest-readiness standards")
    print("  - Press Enter or type 'ALL' for all questions")

    raw_topic = input("Select standard filter: ").strip()
    topic_filter = raw_topic if raw_topic else "ALL"

    raw_limit = input("Number of questions (Enter for all): ").strip()
    limit = int(raw_limit) if raw_limit.isdigit() and int(raw_limit) > 0 else None

    raw_shuffle = input("Shuffle questions? (y/N): ").strip().lower()
    shuffle = raw_shuffle in ("y", "yes", "1")

    return topic_filter, limit, shuffle


def run_interactive_quiz(
    syllabus_path: Path = SYLLABUS_FILE,
    questions_path: Path = QUESTIONS_FILE,
    results_path: Path = RESULTS_FILE,
) -> None:
    syllabus = load_json(syllabus_path)
    questions_data = load_json(questions_path)
    results_data = load_json(results_path, default={"attempts": []})

    all_questions = questions_data.get("questions", [])
    if not all_questions:
        print("No questions found in questions.json.")
        return

    topic_filter, limit, shuffle = prompt_filter(syllabus)
    selected = select_questions(all_questions, syllabus, topic_filter, limit, shuffle)

    if not selected:
        print(f"No questions matched filter '{topic_filter}'.")
        return

    print(f"\nStarting quiz: {len(selected)} question(s). Type 'Q' to quit early.\n")

    session_correct = 0
    session_answered = 0
    new_attempts = []

    for idx, q in enumerate(selected, start=1):
        qid = q.get("id")
        tid = q.get("topic_id")
        std = q.get("standard", tid)
        prompt_text = q.get("question", "")
        options = q.get("options", {})
        correct = str(q.get("correct_answer", "")).strip().upper()
        explanation = q.get("explanation", "")

        print("-" * 75)
        print(f"[{idx}/{len(selected)}] ({qid} | {std} - {tid})")
        print(prompt_text)
        for key in ("A", "B", "C", "D"):
            if key in options:
                print(f"  {key}. {options[key]}")

        while True:
            ans = input("Your answer (A/B/C/D or Q to quit): ").strip().upper()
            if ans == "Q":
                print("\nExiting quiz early and saving completed answers...")
                break
            if ans in options or ans in ("A", "B", "C", "D"):
                break
            print("Invalid input. Please choose A, B, C, D, or Q.")

        if ans == "Q":
            break

        is_correct = ans == correct
        session_answered += 1
        if is_correct:
            session_correct += 1
            print("Result: CORRECT")
        else:
            print(f"Result: INCORRECT (Correct answer: {correct})")

        if explanation:
            print(f"Note: {explanation}")

        new_attempts.append({
            "question_id": qid,
            "topic_id": tid,
            "selected_answer": ans,
            "is_correct": is_correct,
            "answered_at": datetime.now().isoformat(timespec="seconds"),
        })

    if new_attempts:
        results_data.setdefault("attempts", []).extend(new_attempts)
        save_json(results_data, results_path)
        sync_syllabus_metrics(syllabus_path, questions_data, results_data)

    print("\n" + "=" * 75)
    if session_answered > 0:
        session_pct = round((session_correct / session_answered) * 100.0, 1)
        print(f"Session Score: {session_correct}/{session_answered} ({session_pct}%)")
    else:
        print("No questions answered in this session.")
    print(f"Results saved to '{results_path}' and '{syllabus_path}' updated.")
    print("=" * 75)


if __name__ == "__main__":
    run_interactive_quiz()