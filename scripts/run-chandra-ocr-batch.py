#!/usr/bin/env python3
"""Wait for the local structure maker, qualify Chandra OCR 2, then OCR all queued pages.

The model is a maker only. Results remain a staging overlay and never replace
canonical fulltext without later deterministic and visual review.

author: Codex (GPT-5)
date: 2026-08-13
"""

from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
WORKSTATION = LAB.parents[1]
ROOT = LAB / "data/candidates/preembedding-v1"
MODEL = WORKSTATION / "services/chandra-ocr/models/chandra-ocr-2"
MODEL_REVISION = "af93b47dba1b47b6640c86ccf487ed2260ab9a09"
STATUS = ROOT / "chandra-ocr-batch-status.json"
STRUCTURE_STATUS = ROOT / "batch-status.json"
APPLE_STATUS = ROOT / "ocr-batch-status.json"
CONTINUATION_STATUS = ROOT / "checks/continuation-watch-status.json"
CONTINUATION_V2_STATUS = ROOT / "checks/continuation-v2-batch-status.json"
RECOVERY_STATUS = ROOT / "checks/recovery-batch-status.json"
SHARDS = [f"S{i:02d}" for i in range(1, 9)]
CONSOLIDATOR = LAB / "scripts/consolidate-ocr-staging.py"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_status(state: dict) -> None:
    state["updated_at"] = now()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=STATUS.parent,
        prefix=f".{STATUS.name}.", suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(STATUS)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def refresh_manifest(state: dict) -> None:
    completed = subprocess.run(
        [sys.executable, str(CONSOLIDATOR), "--write", "--check"],
        cwd=LAB,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        state.setdefault("manifest_errors", []).append({
            "at": now(), "error": (completed.stderr or completed.stdout).strip()[-1000:]
        })
        write_status(state)


def alpha_ratio(text: str) -> float:
    return round(sum(character.isalpha() for character in text) / max(len(text), 1), 4)


def sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256_image(image) -> str:
    header = json.dumps(
        {"mode": image.mode, "size": list(image.size)}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(header)
    digest.update(image.tobytes())
    return digest.hexdigest()


def package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def chandra_engine_version() -> str:
    import torch

    return (
        f"chandra-ocr={package_version('chandra-ocr')};"
        f"model_revision={MODEL_REVISION};"
        f"transformers={package_version('transformers')};"
        f"torch={torch.__version__};device=mps;dtype=bf16"
    )


def wait_for_structure(state: dict) -> None:
    while True:
        if not STRUCTURE_STATUS.exists():
            state["status"] = "waiting_for_structure_status"
            write_status(state)
            time.sleep(30)
            continue
        structure = read_json(STRUCTURE_STATUS)
        state["structure_status"] = structure.get("status")
        state["structure_processed"] = structure.get("processed_this_run", 0)
        if structure.get("status") == "complete":
            return
        if structure.get("status") in {"failed", "stopped_no_progress"}:
            state["status"] = "blocked_by_structure_batch"
            write_status(state)
            raise SystemExit("structure batch did not complete")
        state["status"] = "waiting_for_structure_batch"
        write_status(state)
        time.sleep(30)


def wait_for_continuations(state: dict) -> None:
    while True:
        if CONTINUATION_V2_STATUS.exists() and RECOVERY_STATUS.exists():
            continuation_v2 = read_json(CONTINUATION_V2_STATUS)
            recovery = read_json(RECOVERY_STATUS)
            state["continuation_v2_status"] = continuation_v2.get("status")
            state["continuation_v2_remaining"] = continuation_v2.get("remaining")
            state["recovery_status"] = recovery.get("status")
            state["recovery_remaining"] = recovery.get("remaining")
            if (
                continuation_v2.get("status") == "complete"
                and continuation_v2.get("remaining") == 0
                and recovery.get("status") == "complete"
                and recovery.get("remaining") == 0
            ):
                state["continuation_status_authority"] = "continuation-v2-plus-recovery"
                write_status(state)
                return
        if not CONTINUATION_STATUS.exists():
            state["status"] = "waiting_for_continuation_status"
            write_status(state)
            time.sleep(30)
            continue
        continuation = read_json(CONTINUATION_STATUS)
        state["continuation_status"] = continuation.get("status")
        state["continuation_receipts_passed"] = continuation.get("receipts_passed", 0)
        state["continuation_remaining"] = continuation.get("remaining", 41)
        if continuation.get("status") == "complete" and continuation.get("remaining") == 0:
            return
        if continuation.get("status") in {"blocked_by_primary_batch", "failed_after_retries"}:
            state["status"] = "blocked_by_continuation_batch"
            write_status(state)
            raise SystemExit("continuation structure batch did not complete")
        state["status"] = "waiting_for_continuation_batch"
        write_status(state)
        time.sleep(30)


def stop_apple_vision_batch() -> None:
    for pattern in ("scripts/run-local-ocr-worker.py", "scripts/run-local-ocr-batch.py"):
        completed = subprocess.run(["pgrep", "-f", pattern], text=True, capture_output=True)
        for value in completed.stdout.split():
            pid = int(value)
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    if APPLE_STATUS.exists():
        apple = read_json(APPLE_STATUS)
        apple["status"] = "replaced_by_chandra_after_qualification"
        apple["replaced_at"] = now()
        temporary = APPLE_STATUS.with_suffix(".tmp")
        temporary.write_text(json.dumps(apple, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(APPLE_STATUS)


def make_candidate(manager, row: dict, source: Path, max_output_tokens: int) -> dict:
    import torch
    from chandra.input import load_pdf_images
    from chandra.model.schema import BatchInputItem

    started = time.monotonic()
    input_sha256 = None
    try:
        # Chandra's current page-range helper consumes zero-based PDF indexes.
        image = load_pdf_images(str(source), [row["pdf_page"] - 1])[0]
        input_sha256 = sha256_image(image)
        result = manager.generate(
            [BatchInputItem(image=image, prompt_type="ocr_layout")],
            max_output_tokens=max_output_tokens,
            include_images=False,
            include_headers_footers=True,
        )[0]
        markdown = result.markdown
        if not markdown.strip():
            disposition = "no_text_detected"
        elif len(markdown) > row["character_count"] * 1.2 and alpha_ratio(markdown) >= row["alpha_ratio"]:
            disposition = "candidate_quality_gain"
        else:
            disposition = "no_deterministic_quality_gain"
        output_sha256 = sha256_json({
            "chunks": result.chunks,
            "markdown": markdown,
            "page_box": result.page_box,
            "raw_html": result.raw,
            "token_count": result.token_count,
        })
        receipt = {
            "schema_version": "1.0",
            "source_id": row["source_id"],
            "volume": row["volume"],
            "pdf_page": row["pdf_page"],
            "owner_shard": row["owner_shard"],
            "source_pdf": str(source),
            "source_text_sha256": row["text_sha256"],
            "input_sha256": input_sha256,
            "model": str(MODEL),
            "engine": "chandra-ocr 0.2.0 / transformers hf / torch mps bf16",
            "engine_version": chandra_engine_version(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "page_box": result.page_box,
            "token_count": result.token_count,
            "embedded_character_count": row["character_count"],
            "ocr_character_count": len(markdown),
            "ocr_alpha_ratio": alpha_ratio(markdown),
            "candidate_disposition": disposition,
            "staging_disposition": disposition,
            "terminal": True,
            "review_status": "machine_extracted",
            "external_model_calls": 0,
            "incremental_usd": 0,
            "chunks": result.chunks,
            "markdown": markdown,
            "raw_html": result.raw,
            "output_sha256": output_sha256,
            "error": None,
        }
        del result, image
        gc.collect()
        torch.mps.empty_cache()
    except Exception as exc:
        receipt = {
            "schema_version": "1.0",
            "source_id": row["source_id"],
            "volume": row["volume"],
            "pdf_page": row["pdf_page"],
            "owner_shard": row["owner_shard"],
            "source_pdf": str(source),
            "source_text_sha256": row["text_sha256"],
            "input_sha256": input_sha256,
            "model": str(MODEL),
            "engine": "chandra-ocr 0.2.0 / transformers hf / torch mps bf16",
            "engine_version": chandra_engine_version(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "candidate_disposition": "ocr_error",
            "staging_disposition": "ocr_error",
            "terminal": True,
            "review_status": "machine_extracted",
            "external_model_calls": 0,
            "incremental_usd": 0,
            "chunks": [],
            "markdown": "",
            "raw_html": "",
            "output_sha256": sha256_json({"chunks": [], "markdown": "", "raw_html": ""}),
            "error": f"{type(exc).__name__}:{str(exc)[:1000]}",
        }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return receipt


def save_candidate(receipt: dict) -> Path:
    shard = receipt["owner_shard"]
    output_dir = ROOT / "shards" / shard / "maker/chandra-ocr-candidates"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{receipt['source_id']}__p{receipt['pdf_page']:04d}.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output


def apple_receipt_is_terminal(row: dict) -> bool:
    artifact = (
        ROOT
        / "shards"
        / row["owner_shard"]
        / "maker/ocr-candidates"
        / f"{row['source_id']}__p{row['pdf_page']:04d}.json"
    )
    if not artifact.exists():
        return False
    try:
        receipt = read_json(artifact)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        receipt.get("terminal") is True
        and receipt.get("staging_disposition")
        in {
            "candidate_quality_gain",
            "no_deterministic_quality_gain",
            "no_text_detected",
            "ocr_error",
        }
        and receipt.get("engine")
        and receipt.get("engine_version")
        and receipt.get("input_sha256")
        and receipt.get("output_sha256")
        and receipt.get("receipt_sha256")
    )


def main() -> None:
    state = {
        "schema_version": "1.0",
        "started_at": now(),
        "status": "initializing",
        "model": str(MODEL),
        "model_revision": MODEL_REVISION,
        "license_scope": "modified OpenRAIL-M; personal/research and qualifying startups only",
        "processed_this_run": 0,
        "dispositions": {},
        "current_page": None,
        "qualification": None,
        "manifest_errors": [],
    }
    write_status(state)
    wait_for_structure(state)
    wait_for_continuations(state)

    subprocess.run([str(WORKSTATION / "service"), "qwen", "off"], cwd=WORKSTATION, check=False)
    state["status"] = "loading_chandra"
    write_status(state)

    os.environ["MODEL_CHECKPOINT"] = str(MODEL)
    os.environ["TORCH_DEVICE"] = "mps"
    os.environ["MAX_OUTPUT_TOKENS"] = "4096"
    from chandra.model import InferenceManager

    manager = InferenceManager(method="hf")
    source_manifest = read_json(LAB / "data/source-manifest.json")
    source_paths = {item["source_id"]: Path(item["path"]) for item in source_manifest["files"]}
    rows = []
    for shard in SHARDS:
        rows.extend(read_jsonl(ROOT / "shards" / shard / "inputs/ocr-pages.jsonl"))
    rows.sort(key=lambda row: (row["ocr_priority"], row["source_id"], row["pdf_page"]))

    golden = next(row for row in rows if row["source_id"] == "kohler-volume-2" and row["pdf_page"] == 509)
    golden_receipt = make_candidate(manager, golden, source_paths[golden["source_id"]], 2048)
    golden_path = save_candidate(golden_receipt)
    golden_text = golden_receipt["markdown"].casefold()
    qualified = (
        golden_receipt["candidate_disposition"] == "candidate_quality_gain"
        and "oenanthe" in golden_text
        and "phellandrium" in golden_text
        and bool(golden_receipt["chunks"])
    )
    state["qualification"] = {
        "source_id": golden["source_id"],
        "pdf_page": golden["pdf_page"],
        "artifact": str(golden_path),
        "status": "pass" if qualified else "fail",
        "required_tokens": ["Oenanthe", "Phellandrium"],
    }
    write_status(state)
    refresh_manifest(state)
    if not qualified:
        state["status"] = "qualification_failed_apple_vision_retained"
        write_status(state)
        refresh_manifest(state)
        raise SystemExit("Chandra qualification failed")

    pending_rows = [row for row in rows if not apple_receipt_is_terminal(row)]
    state["pending_queue_after_qualification"] = len(pending_rows)
    if not pending_rows:
        state["status"] = "complete_qualification_passed_no_pending_queue_apple_vision_retained"
        state["current_page"] = None
        write_status(state)
        refresh_manifest(state)
        print(json.dumps(state, ensure_ascii=False))
        return

    stop_apple_vision_batch()
    state["status"] = "running"
    write_status(state)
    for row in pending_rows:
        output = ROOT / "shards" / row["owner_shard"] / "maker/chandra-ocr-candidates" / f"{row['source_id']}__p{row['pdf_page']:04d}.json"
        if output.exists():
            continue
        state["current_page"] = f"{row['source_id']}:p{row['pdf_page']:04d}"
        write_status(state)
        receipt = make_candidate(manager, row, source_paths[row["source_id"]], 4096)
        save_candidate(receipt)
        state["processed_this_run"] += 1
        disposition = receipt["candidate_disposition"]
        state["dispositions"][disposition] = state["dispositions"].get(disposition, 0) + 1
        write_status(state)
        refresh_manifest(state)

    state["status"] = "complete"
    state["current_page"] = None
    write_status(state)
    refresh_manifest(state)
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
