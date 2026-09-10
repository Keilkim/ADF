"""Verify independent packaged merge/split windows and private Explorer requests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

import pymupdf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    results = {}
    with tempfile.TemporaryDirectory(prefix="adf-frozen-tools-") as directory:
        folder = Path(directory)
        paths = [folder / "첫 번째 자료.pdf", folder / "두 번째 자료 & 검토.pdf"]
        for index, path in enumerate(paths):
            with pymupdf.open() as document:
                for page in range(index + 1):
                    document.new_page().insert_text((72, 72), f"Document {index + 1} - Page {page + 1}")
                document.save(path)
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        env["LOCALAPPDATA"] = str(folder / "local-appdata")
        windows_root = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.path.join(windows_root, "System32") + os.pathsep + windows_root
        requests = Path(env["LOCALAPPDATA"]) / "ADF" / "ShellRequests"
        requests.mkdir(parents=True)
        cases = (("direct_merge", "merge", paths), ("direct_split", "split", paths[:1]),
                 ("request_merge", "merge", paths[::-1]), ("request_split", "split", paths[1:]))
        for label, operation, selected in cases:
            result_file = args.report.with_name(args.report.stem + "-" + label + ".json")
            request = None
            if label.startswith("request_"):
                request = requests / f"request-{uuid.uuid4()}.json"
                request.write_text(json.dumps({"operation": operation, "files": list(map(str, selected))}, ensure_ascii=False), encoding="utf-8")
                command = ["--shell-request", str(request)]
            else:
                command = ["--" + operation, *map(str, selected)]
            command += ["--tool-smoke-test", str(result_file)]
            subprocess.run([str(args.executable.resolve()), *command], env=env, timeout=30, check=True,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            actual = json.loads(result_file.read_text(encoding="utf-8"))
            assert actual["ok"] and actual["standalone"] and actual["frozen"], actual
            assert not actual["main_window_visible"], actual
            assert actual["operation"] == operation
            assert list(map(lambda p: str(Path(p).resolve()), actual["files"])) == list(map(lambda p: str(p.resolve()), selected))
            assert actual["selected_file_count"] == len(selected)
            if request is not None:
                assert not request.exists(), "The one-time request was not consumed"
            results[label] = True
        assert hashes == [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    args.report.write_text(json.dumps({"ok": True, "cases": results, "preserves_sources": True}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "cases": results}))


if __name__ == "__main__":
    main()
