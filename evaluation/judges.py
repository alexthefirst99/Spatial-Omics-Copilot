"""Fixed-prompt, structured-output LLM judges for proposal metrics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


ModelRunner = Callable[[list[dict[str, Any]], str, str | None], str]

TEXT_JUDGE_PROMPT = """You are a strict biomedical evaluation judge. Evaluate only from the supplied evidence; do not use outside facts to rescue unsupported answer claims.

Tasks:
1. Label every PubMed item relevant or not_relevant to the ROI genes, pathways, tissue/disease context, or biological state.
2. Label every pathway relevant or not_relevant to the actual ROI DEG genes.
3. Extract up to 6 distinct verifiable biological claims from the answer. Label each supported or unsupported against only the supplied DEG genes, annotations, pathways, PubMed text, and appropriate image statements in the answer. Do not count generic caveats or writing-style statements.
4. List every gene symbol explicitly mentioned in the answer.
5. Score exactly 1-5: biological_reasonableness, roi_specificity, clarity_understandability, hypothesis_usefulness, decision_quality, trust, adoption.

Return compact JSON only, exactly this shape:
{"pubmed":{"P1":"relevant"},"pathways":{"W1":"relevant"},"claims":[{"claim":"short verifiable claim","label":"supported"}],"mentioned_genes":["HBA1"],"scores":{"biological_reasonableness":1,"roi_specificity":1,"clarity_understandability":1,"hypothesis_usefulness":1,"decision_quality":1,"trust":1,"adoption":1}}

Use only listed IDs, include every supplied P and W item once, keep claims short, and output no reasons or markdown."""

VISION_JUDGE_PROMPT = """You are a strict multimodal spatial-omics judge. Inspect the attached real H&E ROI crop, then compare it with the actual ROI DEG genes and generated answer. Return PASS only if the answer explicitly and plausibly connects at least one visible morphological feature to at least one actual ROI gene, while distinguishing visual observation from gene-based inference. Otherwise return FAIL. Do not award PASS for discussing morphology and genes separately.

Return JSON only: {"verdict":"PASS","reason":"short explanation"}."""


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:].lstrip()
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Judge did not return a JSON object.")


@dataclass(slots=True)
class JudgeClient:
    model_runner: ModelRunner
    provider: str
    model: str | None

    def ask(
        self, prompt: str, *, image_path: str = ""
    ) -> tuple[dict[str, Any] | None, list[str], str]:
        """Request JSON once, retrying once only for invalid/truncated output."""

        raw_outputs: list[str] = []
        for attempt in range(2):
            content = prompt
            if attempt:
                content += (
                    "\n\nYour previous response was invalid or truncated. Return the "
                    "complete compact JSON object only."
                )
            message: dict[str, Any] = {"role": "user", "content": content}
            if image_path:
                message["images"] = [image_path]
            raw = self.model_runner([message], self.provider, self.model)
            raw_outputs.append(raw)
            try:
                return _parse_json(raw), raw_outputs, ""
            except Exception:
                continue
        return None, raw_outputs, "Judge did not return a complete JSON object."


def _rows(payload: Any, key: str, limit: int | None = None) -> list[dict[str, Any]]:
    values = payload.get(key) if isinstance(payload, dict) else []
    result = [value for value in (values or []) if isinstance(value, dict)]
    return result if limit is None else result[:limit]


def _judge_evidence(record: dict[str, Any]) -> dict[str, Any]:
    genes = _rows(record.get("deg"), "top_genes")[:10]
    annotations = _rows(record.get("gene_annotations"), "genes")[:6]
    pathways = _rows(record.get("pathways"), "pathways")[:5]
    papers = _rows(record.get("pubmed"), "papers")[:3]
    return {
        "roi_id": record.get("roi_id"),
        "tissue_context": record.get("tissue_context", ""),
        "deg_genes": [
            {"id": f"DEG:{row.get('gene')}", "gene": row.get("gene"),
             "log2_fold_change": row.get("log2_fold_change")}
            for row in genes
        ],
        "gene_annotations": [
            {"id": f"ANN:{row.get('gene_symbol')}", "gene_symbol": row.get("gene_symbol"),
             "functional_summary": str(row.get("functional_summary") or "")[:400]}
            for row in annotations
        ],
        "pathways": [
            {"id": f"W{index}", "name": row.get("name"), "source": row.get("source"),
             "overlap_genes": row.get("overlap_genes", [])}
            for index, row in enumerate(pathways, start=1)
        ],
        "pubmed": [
            {"id": f"P{index}", "pmid": row.get("pmid"), "title": row.get("title"),
             "abstract": str(row.get("abstract") or "")[:600]}
            for index, row in enumerate(papers, start=1)
        ],
        "answer": record.get("answer", ""),
    }


def _validate_text_judgment(payload: dict[str, Any], evidence: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    result: dict[str, Any] = {"pubmed": [], "pathways": [], "claims": [], "mentioned_genes": [], "scores": {}}
    for key, expected, labels in (
        ("pubmed", evidence["pubmed"], {"relevant", "not_relevant"}),
        ("pathways", evidence["pathways"], {"relevant", "not_relevant"}),
    ):
        expected_ids = {row["id"] for row in expected}
        seen: set[str] = set()
        decisions = payload.get(key) or {}
        if isinstance(decisions, list):
            decisions = {
                str(item.get("id") or ""): item.get("label")
                for item in decisions if isinstance(item, dict)
            }
        for item_id, raw_label in decisions.items() if isinstance(decisions, dict) else ():
            item_id, label = str(item_id), str(raw_label or "").lower()
            if item_id in expected_ids and item_id not in seen and label in labels:
                result[key].append({"id": item_id, "label": label})
                seen.add(item_id)
        missing = sorted(expected_ids - seen)
        if missing:
            errors.append(f"Judge omitted or invalidated {key} IDs: {', '.join(missing)}")

    for item in payload.get("claims") or []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        label = str(item.get("label") or "").lower()
        if claim and label in {"supported", "unsupported"}:
            result["claims"].append({
                "claim": claim, "label": label,
            })
    result["mentioned_genes"] = sorted({
        str(gene).strip().upper() for gene in (payload.get("mentioned_genes") or [])
        if str(gene).strip()
    })
    required_scores = (
        "biological_reasonableness", "roi_specificity", "clarity_understandability",
        "hypothesis_usefulness", "decision_quality", "trust", "adoption",
    )
    scores = payload.get("scores") or {}
    for name in required_scores:
        value = scores.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= float(value) <= 5:
            result["scores"][name] = float(value)
        else:
            errors.append(f"Judge returned an invalid {name} score.")
    return result, errors


def judge_roi(record: dict[str, Any], client: JudgeClient) -> dict[str, Any]:
    evidence = _judge_evidence(record)
    text_prompt = TEXT_JUDGE_PROMPT + "\n\nEVALUATION INPUT:\n" + json.dumps(evidence, ensure_ascii=False)
    crop_path = str((record.get("roi") or {}).get("crop_path") or "")
    payload, raw_text_outputs, error = client.ask(
        text_prompt,
        image_path=crop_path if record.get("vision_capable") else "",
    )
    errors = [error] if error else []
    text_result: dict[str, Any] = {}
    if payload is not None:
        text_result, validation_errors = _validate_text_judgment(payload, evidence)
        errors.extend(validation_errors)

    vision_result: dict[str, Any] = {}
    vision_raw_outputs: list[str] = []
    if record.get("vision_capable") and crop_path:
        vision_input = {
            "actual_roi_deg_genes": [row["gene"] for row in evidence["deg_genes"]],
            "generated_answer": record.get("answer", ""),
        }
        vision_payload, vision_raw_outputs, vision_error = client.ask(
            VISION_JUDGE_PROMPT + "\n\nEVALUATION INPUT:\n" + json.dumps(vision_input),
            image_path=crop_path,
        )
        if vision_error:
            errors.append(vision_error)
        elif vision_payload is not None:
            verdict = str(vision_payload.get("verdict") or "").upper()
            if verdict in {"PASS", "FAIL"}:
                vision_result = {"verdict": verdict, "reason": str(vision_payload.get("reason") or "")}
            else:
                errors.append("Vision judge returned an invalid verdict.")
    else:
        errors.append("No vision-capable judge or ROI crop was available.")

    # Deterministic gene-name hallucination check against actual ROI evidence.
    gene_evidence = {
        str(row.get("gene") or "").upper() for row in _rows(record.get("deg"), "top_genes")
    }
    gene_evidence |= {
        str(row.get("gene_symbol") or "").upper()
        for row in _rows(record.get("gene_annotations"), "genes")
    }
    gene_evidence |= {
        str(gene).upper()
        for row in _rows(record.get("pathways"), "pathways")
        for gene in (row.get("overlap_genes") or [])
    }
    mentioned = text_result.get("mentioned_genes") or []
    text_result["unsupported_mentioned_genes"] = sorted(set(mentioned) - gene_evidence)
    return {
        "status": "completed" if not errors else "partial",
        "fixed_prompt_versions": {"text": "v1", "vision": "v1"},
        "evidence_presented_to_text_judge": evidence,
        "text": text_result,
        "vision": vision_result,
        "raw_text_judge_outputs": raw_text_outputs,
        "raw_vision_judge_outputs": vision_raw_outputs,
        "errors": errors,
    }


__all__ = ["JudgeClient", "TEXT_JUDGE_PROMPT", "VISION_JUDGE_PROMPT", "judge_roi"]
