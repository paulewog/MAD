"""Feature file state management — JSON-based storage for feature data."""

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from config import Config

logger = logging.getLogger("pipeline")

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
MAX_HISTORY_NOTE_LENGTH = 100


STAGES = [
    "ideas", "ideating", "plan-inbox", "reviewing-plan", "requested-input", "approved", "spec-writing",
    "implementing", "testing", "review", "final-human-approval", "done", "rejected",
]

STAGE_ACTIONS = {
    "plan": ["plan-inbox", "reviewing-plan", "requested-input", "approved"],
    "implement": ["approved", "spec-writing"],
    "ideate": ["ideas"],
}


def _slugify(title: str) -> str:
    """Convert a title to a filename-safe slug."""
    import re
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


class FeatureFile:
    """Represents a single feature file stored as JSON.
    
    The file is a JSON object with structured fields.
    The parent directory name determines the current pipeline stage.
    """

    def __init__(self, path: Path):
        self._path = path.resolve()
        with open(self._path, "r") as f:
            self._data = json.load(f)

    def _save(self) -> None:
        """Internal save - writes to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    # --- Properties ---

    @property
    def path(self) -> Path:
        return self._path

    @property
    def id(self) -> str:
        return self._data.get("id", "")

    @property
    def board(self) -> str:
        return self._data.get("board", "")

    @property
    def title(self) -> str:
        return self._data.get("title", "")

    @property
    def item_type(self) -> str:
        """Returns 'feature' or 'bug'. Defaults to 'feature' for existing items."""
        return self._data.get("type", "feature")

    @property
    def created(self) -> str:
        return self._data.get("created", "")

    @property
    def slug(self) -> str:
        return self._path.stem

    @property
    def current_stage(self) -> str:
        """Derived from the parent directory name."""
        return self._path.parent.name

    @property
    def design_ref(self) -> str:
        return self._data.get("design_ref", "")

    def set_design_ref(self, ref: str) -> None:
        self._data["design_ref"] = ref
        self._save()

    @property
    def done_script(self) -> str:
        return self._data.get("done_script", "")

    def set_done_script(self, script_id: str) -> None:
        if script_id:
            from scripts import load_scripts
            config = Config()
            scripts = load_scripts(config.mad_dir)
            script = next((s for s in scripts if s.id == script_id), None)
            if not script:
                import logging
                logging.getLogger(__name__).warning(
                    f"Done script '{script_id}' not found in scripts.json"
                )
        self._data["done_script"] = script_id or ""
        self._save()

    # --- Field accessors ---

    @property
    def description(self) -> str:
        return str(self._data.get("description", ""))

    def set_description(self, value: str) -> None:
        self._data["description"] = value
        self._save()

    def set_title(self, new_title: str) -> None:
        if not new_title.strip():
            raise ValueError("Title cannot be empty")
        self._data["title"] = new_title
        self._save()
        self.add_history(self.current_stage.upper(), "Title edited")

    def set_item_type(self, new_type: str) -> None:
        if new_type not in ("feature", "bug"):
            raise ValueError('Type must be "feature" or "bug"')
        self._data["type"] = new_type
        self._save()
        self.add_history(self.current_stage.upper(), "Type edited")

    @property
    def plan(self) -> str:
        return self._data.get("plan", "")

    def set_plan(self, value: str) -> None:
        self._data["plan"] = value
        self._save()

    @property
    def plan_exploration_summary(self) -> str:
        return self._data.get("plan_exploration_summary", "")

    def set_plan_exploration_summary(self, value: str) -> None:
        self._data["plan_exploration_summary"] = value
        self._save()

    @property
    def impl_spec(self):
        return self._data.get("impl_spec", "")

    def set_impl_spec(self, value) -> None:
        self._data["impl_spec"] = value
        self._save()

    @property
    def test_spec(self):
        return self._data.get("test_spec", "")

    def set_test_spec(self, value) -> None:
        self._data["test_spec"] = value
        self._save()

    @property
    def impl_notes(self) -> str:
        return self._data.get("impl_notes", "")

    def set_impl_notes(self, value: str) -> None:
        self._data["impl_notes"] = value
        self._save()

    @property
    def ideation_summaries(self) -> list:
        return self._data.get("ideation_summaries", [])

    def add_ideation_summary(self, summary: str) -> None:
        if "ideation_summaries" not in self._data:
            self._data["ideation_summaries"] = []
        self._data["ideation_summaries"].append(summary)
        self._save()

    @property
    def Ideation(self) -> str:
        return self._data.get("Ideation", "")

    def set_Ideation(self, value: str) -> None:
        self._data["Ideation"] = value
        self._save()

    @property
    def test_results(self) -> dict:
        return self._data.get("test_results", {})

    def set_test_results(self, value: dict) -> None:
        self._data["test_results"] = value
        self._save()

    @property
    def plan_reviews(self) -> list:
        return self._data.get("plan_reviews", [])

    @property
    def impl_reviews(self) -> list:
        return self._data.get("impl_reviews", [])

    def add_plan_review(self, verdict: str, summary: str, feedback: str | None) -> None:
        normalized = "PASS" if verdict and verdict.upper() == "PASS" else "FAIL"
        if "plan_reviews" not in self._data:
            self._data["plan_reviews"] = []
        self._data["plan_reviews"].append({
            "ts": _now_iso(),
            "verdict": normalized,
            "summary": summary,
            "feedback": feedback if feedback else None,
        })
        self._save()

    def add_impl_review(self, verdict: str, summary: str, feedback: str | None) -> None:
        normalized = "PASS" if verdict and verdict.upper() == "PASS" else "FAIL"
        if "impl_reviews" not in self._data:
            self._data["impl_reviews"] = []
        self._data["impl_reviews"].append({
            "ts": _now_iso(),
            "verdict": normalized,
            "summary": summary,
            "feedback": feedback if feedback else None,
        })
        self._save()

    def get_latest_plan_review(self) -> dict | None:
        reviews = self._data.get("plan_reviews", [])
        return reviews[-1] if reviews else None

    def get_latest_impl_review(self) -> dict | None:
        reviews = self._data.get("impl_reviews", [])
        return reviews[-1] if reviews else None

    @property
    def questions(self) -> list:
        """Returns list of questions needing human input."""
        return self._data.get("questions", [])

    def set_questions(self, value: list) -> None:
        """Set questions, preserving any that have already been answered."""
        existing = {q.get("question"): q.get("answer") for q in self._data.get("questions", [])}
        merged = []
        for q in value:
            q_text = q.get("question", "")
            prev_answer = existing.get(q_text, "")
            merged.append({
                "question": q_text,
                "answer": prev_answer
            })
        self._data["questions"] = merged
        self._save()

    def add_question(self, question: str) -> None:
        """Add a question needing human input."""
        if "questions" not in self._data:
            self._data["questions"] = []
        self._data["questions"].append({
            "question": question,
            "answer": ""
        })
        self._save()

    def answer_question(self, index: int, answer: str) -> None:
        """Answer a question by index."""
        questions = self._data.get("questions", [])
        if 0 <= index < len(questions):
            questions[index]["answer"] = answer
            self._data["questions"] = questions
            self._save()

    @property
    def history(self) -> str:
        """Returns history as formatted string for backward compatibility."""
        entries = self._data.get("history", [])
        lines = []
        for entry in entries:
            ts = entry.get("ts", "")
            stage = entry.get("stage", "")
            note = entry.get("note", "")
            lines.append(f"- {ts} | {stage} | {note}")
        return "\n".join(lines)

    def add_history(self, stage: str, note: str) -> None:
        """Add a timestamped entry to history."""
        ts = _now_iso()
        if "history" not in self._data:
            self._data["history"] = []
        # Strip ANSI escape codes and truncate
        clean_note = _ANSI_RE.sub('', note).strip()
        if len(clean_note) > MAX_HISTORY_NOTE_LENGTH:
            clean_note = clean_note[:MAX_HISTORY_NOTE_LENGTH - 3] + "..."
        self._data["history"].append({
            "ts": ts,
            "stage": stage.upper(),
            "note": clean_note,
        })
        self._save()

    @property
    def pipeline_log(self) -> str:
        """Returns pipeline log as formatted string for backward compatibility."""
        entries = self._data.get("pipeline_log", [])
        lines = []
        for entry in entries:
            ts = entry.get("ts", "")
            phase = entry.get("phase", "")
            output = entry.get("output", "")
            lines.append(f"### {ts} — {phase}\n\n{output}\n")
        return "\n".join(lines)

    def append_pipeline_log(self, phase: str, output: str) -> None:
        """Append agent output to pipeline log."""
        ts = _now_iso()
        if "pipeline_log" not in self._data:
            self._data["pipeline_log"] = []
        self._data["pipeline_log"].append({
            "ts": ts,
            "phase": phase,
            "output": output,
        })
        self._save()

    # --- Backward compatibility aliases ---

    def get_section(self, name: str) -> str:
        """Backward compat: get a section by name."""
        mapping = {
            "Description": "description",
            "Plan": "plan",
            "Implementation Spec": "impl_spec",
            "Test Spec": "test_spec",
            "Implementation Notes": "impl_notes",
            "History": "history",
            "Pipeline Log": "pipeline_log",
        }
        key = mapping.get(name, name.lower().replace(" ", "_"))
        if key in ("history", "pipeline_log"):
            # These need special handling
            if key == "history":
                return self.history
            return self.pipeline_log
        return self._data.get(key, "")

    def set_section(self, name: str, content: str) -> None:
        """Backward compat: set a section by name."""
        mapping = {
            "Description": "description",
            "Plan": "plan",
            "Implementation Spec": "impl_spec",
            "Test Spec": "test_spec",
            "Implementation Notes": "impl_notes",
        }
        key = mapping.get(name, name.lower().replace(" ", "_"))
        self._data[key] = content
        self._save()

    # --- Persistence ---

    def save(self) -> None:
        """Write the feature file back to its current path."""
        self._save()

    def move_to_stage(self, stage: str) -> None:
        """Move the file to the target stage directory under the same board."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}. Valid: {STAGES}")

        config = Config()
        target_dir = config.boards_dir / self.board / stage
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / self._path.name

        shutil.move(str(self._path), str(target_path))
        self._path = target_path.resolve()

    # --- Static finders ---

    @staticmethod
    def create(board: str, title: str, description: str = "", item_type: str = "feature", done_script: str = "") -> "FeatureFile":
        """Create a new feature file in the board's ideas stage.

        Args:
            item_type: "feature" (default) or "bug"
            done_script: Script ID to run when item is completed (optional)
        """
        config = Config()
        slug = _slugify(title)
        feature_id = uuid.uuid4().hex[:8]
        ideas_dir = config.boards_dir / board / "ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        path = ideas_dir / f"{slug}.json"

        data = {
            "id": feature_id,
            "board": board,
            "title": title,
            "type": item_type,
            "created": _now_iso(),
            "description": description or "No description provided.",
            "done_script": done_script,
            "plan": "",
            "plan_exploration_summary": "",
            "impl_spec": "",
            "test_spec": "",
            "impl_notes": "",
            "ideation_summaries": [],
            "Ideation": "",
            "history": [
                {"ts": _now_iso(), "stage": "IDEAS", "note": "Idea created"}
            ],
            "pipeline_log": [],
            "plan_reviews": [],
            "impl_reviews": [],
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

        return FeatureFile(path)

    @staticmethod
    def find(query: str) -> Optional["FeatureFile"]:
        """Search all boards/stages for a feature matching by id, slug, or partial slug."""
        query_lower = query.lower().strip()
        all_features = FeatureFile.list_all()

        for f in all_features:
            if f.slug == query_lower:
                return f

        for f in all_features:
            if f.id == query_lower:
                return f

        matches = [f for f in all_features if query_lower in f.slug]
        if matches:
            matches.sort(key=lambda f: len(f.slug))
            return matches[0]

        return None

    @staticmethod
    def list_all(board: str = None, stage: str = None) -> List["FeatureFile"]:
        """List all feature files, optionally filtered by board and/or stage."""
        config = Config()
        results = []

        boards = [board] if board else config.boards
        stages_to_check = [stage] if stage else STAGES

        for b in boards:
            for s in stages_to_check:
                stage_dir = config.boards_dir / b / s
                if not stage_dir.exists():
                    continue
                for json_file in sorted(stage_dir.glob("*.json")):
                    try:
                        results.append(FeatureFile(json_file))
                    except (json.JSONDecodeError, KeyError, ValueError, PermissionError, OSError) as e:
                        logger.error(f"Failed to load feature file {json_file}: {e}")
                        continue

        return results
