"""Validate the handoff package only. No network, LLM, or application tests."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit("MANIFEST.json is missing; this is not the complete handoff package.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root):
            errors.append(f"Unsafe path: {item['path']}")
            continue
        if not path.is_file():
            errors.append(f"Missing: {item['path']}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            errors.append(f"Changed: {item['path']}")
    # Smoke-check the explicitly non-live examples.
    candidate = json.loads((root / "examples/candidate.synthetic.json").read_text(encoding="utf-8"))
    selection = json.loads((root / "examples/editorial_selection.synthetic.json").read_text(encoding="utf-8"))
    if candidate.get("is_fixture") is not True or candidate.get("score_total") is not None:
        errors.append("Synthetic candidate must remain clearly marked and unscored.")
    if selection.get("selected_event_id") is not None or selection.get("selected_fact_ids"):
        errors.append("Synthetic selection should illustrate the default unselected state.")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {len(manifest['files'])} handoff files match the manifest.")
    print("Package integrity only; no application, live-source, LLM or performance tests were run.")

if __name__ == "__main__":
    main()
