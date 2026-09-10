"""Exercise exports through a packaged executable with a developer-side oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pymupdf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    report = {"ok": False, "worker_merge": False, "worker_split": False, "worker_compress": False,
              "preserves_sources": False, "rejects_overwrite": False}
    with tempfile.TemporaryDirectory(prefix="adf-frozen-worker-") as temporary:
        folder = Path(temporary)
        fixtures = []
        for name, texts in (("한글 원본 A.pdf", ["First document page one", "First document page two"]), ("한글 원본 B.pdf", ["Second document page one"])):
            path = folder / name
            with pymupdf.open() as document:
                for value in texts:
                    document.new_page().insert_text((72, 72), value)
                document.save(path)
            fixtures.append(path)
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in fixtures}

        def invoke(task: dict, failure: bool = False) -> dict:
            request = folder / "task.json"
            output = folder / "result.json"
            output.unlink(missing_ok=True)
            task["result"] = str(output)
            request.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
            if os.name == "nt":
                windows_root = os.environ.get("SystemRoot", r"C:\Windows")
                env["PATH"] = os.path.join(windows_root, "System32") + os.pathsep + windows_root
            run = subprocess.run([str(executable), "--worker", str(request)], env=env, timeout=60,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            if failure:
                assert run.returncode != 0 and "error" in result, result
            else:
                assert run.returncode == 0 and "error" not in result, result
            return result

        merged = folder / "병합 결과.pdf"
        merge_task = {"operation": "merge", "paths": [str(path) for path in fixtures], "output": str(merged)}
        invoke(merge_task)
        with pymupdf.open(merged) as document:
            assert len(document) == 3
            assert "First document page one" in document[0].get_text()
            assert "Second document page one" in document[2].get_text()
        report["worker_merge"] = True
        previous = hashlib.sha256(merged.read_bytes()).hexdigest()
        invoke(merge_task, failure=True)
        assert previous == hashlib.sha256(merged.read_bytes()).hexdigest()
        report["rejects_overwrite"] = True

        parts = folder / "분리 결과"
        parts.mkdir()
        result = invoke({"operation": "split", "source": str(merged), "groups": [[0, 1], [2]],
                         "output_dir": str(parts), "stem": "분리"})
        assert len(result["result"]) == 2
        for path, expected in zip(result["result"], (2, 1)):
            with pymupdf.open(path) as document:
                assert len(document) == expected
        report["worker_split"] = True

        extracted = folder / '선택 페이지.pdf'
        invoke({'operation':'extract', 'source':str(merged), 'pages':[0, 2], 'output':str(extracted)})
        with pymupdf.open(extracted) as document:
            assert document.page_count == 2
            assert 'First document page one' in document[0].get_text()
            assert 'Second document page one' in document[1].get_text()
        report['worker_extract'] = True

        compressed = folder / "최적화 결과.pdf"
        invoke({"operation": "compress", "source": str(merged), "output": str(compressed),
                "options": {"mode": "images", "dpi": 144, "quality": 75}})
        with pymupdf.open(compressed) as document:
            assert len(document) == 3
            assert "First document page one" in document[0].get_text()
        report["worker_compress"] = True
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in hashes.items())
        report["preserves_sources"] = True
        report["ok"] = True
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
