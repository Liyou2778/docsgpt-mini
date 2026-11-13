#!/usr/bin/env python3
"""Evaluation harness for docsgpt-mini.

Runs against a live server over HTTP (the same API the UI uses):

1. idempotently ingests every ``corpus/*.md`` document and waits until all are
   ``ready``;
2. asks the 20 labelled questions in ``eval/questions.json`` with each fusion
   strategy (``weighted`` and ``rrf``);
3. computes Recall@5 (expected document hit in the top-5, with a keyword
   sanity check on the returned chunk), citation traceability (every ``[n]``
   marker independently verified against chunk ids fetched from the documents
   API) and latency statistics;
4. writes ``eval/results.json`` and prints README-ready markdown tables.

Usage:
    python eval/eval.py --base-url http://127.0.0.1:8202
"""

import argparse
import json
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus"
QUESTIONS_PATH = ROOT / "eval" / "questions.json"
DEFAULT_OUT = ROOT / "eval" / "results.json"
CITATION_RE = re.compile(r"\[(\d{1,3})\]")
DEFAULT_STRATEGIES = ("weighted", "rrf")


def log(message):
    print(message, flush=True)


def request_json(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError("%s %s -> HTTP %s: %s" % (method, path, response.status_code, response.text[:400]))
    return response.json()


def ensure_corpus(client):
    files = sorted(CORPUS_DIR.glob("*.md"))
    if not files:
        raise RuntimeError("no corpus documents found in %s" % CORPUS_DIR)
    results = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        payload = {"title": path.stem, "filename": path.name, "text": text}
        data = request_json(client, "POST", "/api/docs", json=payload)
        results.append(
            {
                "file": path.name,
                "doc_id": data["document"]["id"],
                "created": data["created"],
                "status": data["document"]["status"],
            }
        )
    return results, files


def wait_ready(client, timeout=180.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        data = request_json(client, "GET", "/api/docs")
        documents = data["documents"]
        last = documents
        failed = [doc for doc in documents if doc["status"] == "failed"]
        if failed:
            details = "; ".join("%s: %s" % (doc["title"], doc["error"]) for doc in failed)
            raise RuntimeError("ingestion failed: %s" % details)
        pending = [doc for doc in documents if doc["status"] in ("pending", "processing")]
        if documents and not pending:
            return documents
        time.sleep(0.25)
    raise RuntimeError("timeout waiting for ingestion, last state: %s" % json.dumps(last, ensure_ascii=False)[:600])


def build_chunk_index(client):
    """Fetch every ready document's chunks: the independent source of truth."""
    documents = request_json(client, "GET", "/api/docs")["documents"]
    chunk_ids = set()
    filename_to_doc = {}
    for doc in documents:
        if doc["status"] != "ready":
            continue
        if doc.get("filename"):
            filename_to_doc[doc["filename"]] = doc["id"]
        detail = request_json(
            client, "GET", "/api/docs/%s" % doc["id"], params={"include_chunks": "true"}
        )
        for chunk in detail["document"].get("chunks", []):
            chunk_ids.add(chunk["chunk_id"])
    return chunk_ids, filename_to_doc, documents


def check_idempotency(client):
    """Use a synthetic document so the check is independent of existing data."""
    marker = uuid.uuid4().hex[:10]
    text = (
        "# 幂等性检查文档\n\n"
        "这是一篇只用于验证重复上传幂等性的临时文档，唯一标记为 %s。"
        "对同一份内容重复调用上传接口时，不应产生第二篇文档。\n" % marker
    )
    payload = {
        "title": "idempotency-check",
        "filename": "idempotency-check.md",
        "text": text,
    }
    first = request_json(client, "POST", "/api/docs", json=payload)
    second = request_json(client, "POST", "/api/docs", json=payload)
    doc_id = first["document"]["id"]

    deadline = time.time() + 60
    while time.time() < deadline:
        detail = request_json(client, "GET", "/api/docs/%s" % doc_id)["document"]
        if detail["status"] == "ready":
            break
        if detail["status"] == "failed":
            raise RuntimeError("idempotency document failed: %s" % detail.get("error"))
        time.sleep(0.2)
    request_json(client, "DELETE", "/api/docs/%s" % doc_id)

    return {
        "first_created": first["created"],
        "second_created": second["created"],
        "same_doc_id": doc_id == second["document"]["id"],
        "cleaned_up": True,
    }


def evaluate_strategy(client, questions, filename_to_doc, chunk_ids, strategy, top_k, alpha):
    records = []
    for index, item in enumerate(questions, 1):
        payload = {"question": item["question"], "strategy": strategy, "top_k": top_k}
        if strategy == "weighted":
            payload["alpha"] = alpha
        started = time.perf_counter()
        data = request_json(client, "POST", "/api/chat", json=payload)
        wall_ms = (time.perf_counter() - started) * 1000.0

        hits = data.get("retrieval", {}).get("hits", [])
        expected_doc_id = filename_to_doc.get(item["expected_doc"])
        keywords = [keyword.lower() for keyword in item["keywords"]]
        hit = False
        top5 = []
        for rank, hit_item in enumerate(hits[:5], 1):
            text_lower = hit_item["text"].lower()
            matched = [keyword for keyword in keywords if keyword in text_lower]
            if expected_doc_id and hit_item["doc_id"] == expected_doc_id and matched:
                hit = True
            top5.append(
                {
                    "rank": rank,
                    "chunk_id": hit_item["chunk_id"],
                    "doc_id": hit_item["doc_id"],
                    "doc_title": hit_item["doc_title"],
                    "seq": hit_item["seq"],
                    "score": hit_item["score"],
                    "bm25_score": hit_item["bm25_score"],
                    "vector_score": hit_item["vector_score"],
                    "keyword_matched": matched,
                }
            )

        citations = data.get("citations", [])
        citation_records = [
            {
                "index": citation["index"],
                "chunk_id": citation["chunk_id"],
                "doc_title": citation["doc_title"],
                "valid": citation["chunk_id"] in chunk_ids and bool(citation["text"].strip()),
            }
            for citation in citations
        ]
        citation_by_index = {record["index"]: record for record in citation_records}
        markers = [int(value) for value in CITATION_RE.findall(data.get("answer", ""))]
        resolved_markers = sum(
            1 for marker in markers if marker in citation_by_index and citation_by_index[marker]["valid"]
        )
        answer_traceable = bool(markers) and resolved_markers == len(markers)

        record = {
            "id": item["id"],
            "question": item["question"],
            "expected_doc": item["expected_doc"],
            "expected_doc_id": expected_doc_id,
            "keywords": item["keywords"],
            "hit": hit,
            "recall_at_5": 1.0 if hit else 0.0,
            "top5": top5,
            "answer": data.get("answer", ""),
            "citations": citation_records,
            "markers": markers,
            "resolved_markers": resolved_markers,
            "citation_traceable": (resolved_markers / len(markers)) if markers else 1.0,
            "answer_level_traceable": answer_traceable,
            "retrieval_latency_ms": data.get("retrieval", {}).get("latency_ms", 0.0),
            "latency_ms": round(wall_ms, 3),
            "llm_provider": data.get("llm", {}).get("provider"),
        }
        records.append(record)
        status = "HIT " if hit else "MISS"
        log(
            "  [%s] %2d/%2d %s %-28s %.1f ms"
            % (strategy, index, len(questions), status, item["expected_doc"], wall_ms)
        )
    return records


def summarize(records):
    total = len(records)
    hits = sum(record["recall_at_5"] for record in records)
    markers = sum(len(record["markers"]) for record in records)
    resolved = sum(record["resolved_markers"] for record in records)
    latencies = [record["latency_ms"] for record in records]
    retrieval_latencies = [record["retrieval_latency_ms"] for record in records]
    citation_counts = [len(record["citations"]) for record in records]
    answers_without_markers = sum(1 for record in records if not record["markers"])
    answer_level = sum(1 for record in records if record["answer_level_traceable"]) / total
    return {
        "questions": total,
        "recall_at_5": round(hits / total, 4),
        "recall_hits": int(hits),
        "markers": markers,
        "resolved_markers": resolved,
        "citation_traceability_rate": round(resolved / markers, 4) if markers else 1.0,
        "answer_level_traceability_rate": round(answer_level, 4),
        "answers_without_citations": answers_without_markers,
        "avg_latency_ms": round(statistics.mean(latencies), 3),
        "p50_latency_ms": round(statistics.median(latencies), 3),
        "p95_latency_ms": round(_percentile(latencies, 95), 3),
        "avg_retrieval_latency_ms": round(statistics.mean(retrieval_latencies), 3),
        "avg_citations": round(statistics.mean(citation_counts), 2),
    }


def _percentile(values, percentile):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def print_tables(questions, runs):
    log("\n## 评估汇总（20 题，top_k=5，mock LLM）\n")
    log("| 策略 | Recall@5 | 引用可溯源率 | 答案级可溯源率 | 平均延迟 | p50 | p95 | 平均检索延迟 | 平均引用数 |")
    log("|---|---|---|---|---|---|---|---|---|")
    for strategy, run in runs.items():
        metric = run["metrics"]
        log(
            "| %s | %.2f | %.2f%% | %.2f%% | %.1f ms | %.1f ms | %.1f ms | %.1f ms | %.2f |"
            % (
                strategy,
                metric["recall_at_5"],
                metric["citation_traceability_rate"] * 100,
                metric["answer_level_traceability_rate"] * 100,
                metric["avg_latency_ms"],
                metric["p50_latency_ms"],
                metric["p95_latency_ms"],
                metric["avg_retrieval_latency_ms"],
                metric["avg_citations"],
            )
        )
    log("\n| # | 问题 | 期望文档 | weighted 命中 | rrf 命中 |")
    log("|---|---|---|---|---|")
    weighted = {record["id"]: record for record in runs.get("weighted", {}).get("records", [])}
    rrf = {record["id"]: record for record in runs.get("rrf", {}).get("records", [])}
    for item in questions:
        qid = item["id"]
        log(
            "| %d | %s | %s | %s | %s |"
            % (
                qid,
                item["question"],
                item["expected_doc"],
                "✅" if weighted.get(qid, {}).get("hit") else "❌",
                "✅" if rrf.get(qid, {}).get("hit") else "❌",
            )
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate docsgpt-mini against a live server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8202")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--strategies", default="weighted,rrf")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    if not QUESTIONS_PATH.exists():
        raise SystemExit("questions file not found: %s" % QUESTIONS_PATH)
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    log("Loaded %d questions from %s" % (len(questions), QUESTIONS_PATH))

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        log("Checking server health ...")
        health = request_json(client, "GET", "/api/health")
        stats = request_json(client, "GET", "/api/stats")
        log("Server ok, llm provider = %s" % health["llm"]["provider"])

        log("Ingesting corpus (idempotent) ...")
        ingested, files = ensure_corpus(client)
        created = sum(1 for item in ingested if item["created"])
        log("  %d corpus files, %d newly created, %d already present" % (len(files), created, len(files) - created))

        log("Waiting for ingestion pipeline to finish ...")
        documents = wait_ready(client)
        chunk_ids, filename_to_doc, _ = build_chunk_index(client)
        ready_docs = [doc for doc in documents if doc["status"] == "ready"]
        chunk_total = sum(doc["chunk_count"] for doc in ready_docs)
        log("  %d ready documents, %d chunks" % (len(ready_docs), chunk_total))
        if len(ready_docs) < 5:
            raise RuntimeError("expected at least 5 ready documents, got %d" % len(ready_docs))

        idempotency = check_idempotency(client)
        log(
            "Idempotency check: first_created=%s, second_created=%s, same_doc_id=%s"
            % (idempotency["first_created"], idempotency["second_created"], idempotency["same_doc_id"])
        )

        runs = {}
        for strategy in strategies:
            log("\nRunning strategy '%s' ..." % strategy)
            records = evaluate_strategy(
                client, questions, filename_to_doc, chunk_ids, strategy, args.top_k, args.alpha
            )
            runs[strategy] = {"metrics": summarize(records), "records": records}

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "corpus": {
            "files": len(files),
            "documents": len(ready_docs),
            "chunks": chunk_total,
        },
        "config": {
            "top_k": args.top_k,
            "alpha": args.alpha,
            "strategies": strategies,
            "llm_provider": health["llm"]["provider"],
            "questions": len(questions),
            "chunk_size": stats["config"]["chunk_size"],
            "chunk_overlap_ratio": stats["config"]["chunk_overlap_ratio"],
            "vector_dim": stats["config"]["vector_dim"],
            "bm25_k1": stats["config"]["bm25_k1"],
            "bm25_b": stats["config"]["bm25_b"],
        },
        "idempotency": idempotency,
        "runs": runs,
    }
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log("\nSaved results to %s" % output_path)

    print_tables(questions, runs)
    for strategy, run in runs.items():
        metric = run["metrics"]
        log(
            "\n%s: recall@5=%.4f traceability=%.4f avg_latency_ms=%.3f"
            % (strategy, metric["recall_at_5"], metric["citation_traceability_rate"], metric["avg_latency_ms"])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
