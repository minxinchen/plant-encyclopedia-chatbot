#!/usr/bin/env python3
"""Build an isolated, fail-closed projection for nine recovery packages."""

from __future__ import annotations

import argparse, hashlib, json, tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

LAB = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = LAB / "data/candidates/preembedding-v1"

def cj(v: Any) -> str: return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def h(v: Any) -> str: return hashlib.sha256(cj(v).encode()).hexdigest()
def oh(v: dict, f: str) -> str: return h({k: x for k, x in v.items() if k != f})
def th(v: str) -> str: return hashlib.sha256(v.encode()).hexdigest()
def rj(p: Path) -> dict: return json.loads(p.read_text(encoding="utf-8"))
def rjl(p: Path) -> list[dict]: return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
def now() -> str: return datetime.now().astimezone().isoformat(timespec="seconds")
def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False) as f: f.write(text); t=Path(f.name)
    t.replace(p)
def wj(p: Path, v: dict) -> None: write(p, json.dumps(v, ensure_ascii=False, indent=2)+"\n")
def wjl(p: Path, vs: list[dict]) -> None: write(p, "".join(json.dumps(v,ensure_ascii=False)+"\n" for v in vs))
def default_receipt_hash(v: dict) -> str:
    return hashlib.sha256(json.dumps({k:x for k,x in v.items() if k!="receipt_sha256"},ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--root",type=Path,default=DEFAULT_ROOT); ap.add_argument("--require-complete",action="store_true"); a=ap.parse_args(); root=a.root
    packages=rjl(root/"structure/content-recovery-work-packages.jsonl")
    parent_body_pages: dict[str, set[int]] = {}
    for item in packages:
        if item.get("recovery_kind") == "terminal_body_boundary_split":
            parent_body_pages.setdefault(item["parent_entry_id"], set()).update(item.get("pdf_pages", []))
    source_path=root/"source-receipt.json"; source=rj(source_path); source_hash={x["source_id"]:x["sha256"] for x in source["sources"]}
    pages={}
    for p in sorted((root/"shards").glob("S*/inputs/pages.jsonl")):
        for x in rjl(p): pages[(x["source_id"],x["pdf_page"])]=x
    global_errors=[]
    if len(packages)!=9 or len({x["package_id"] for x in packages})!=9 or len({x["recovered_entry_id"] for x in packages})!=9: global_errors.append("recovery_package_cardinality_drift")
    checks=[]; candidates=[]
    for pkg in packages:
        pid=pkg["package_id"]; errs=[]
        if pkg.get("package_sha256")!=oh(pkg,"package_sha256") or pkg.get("stage")!="local_structure_recovery": errs.append("package_hash_or_stage_drift")
        if pkg.get("page_count")!=len(pkg.get("pdf_pages",[])) or not 1<=pkg.get("page_count",0)<=6: errs.append("package_page_count_drift")
        if pkg.get("name_resolution_status")!="unresolved" or pkg.get("layout_or_plate_claims_approved") is not False: errs.append("unsafe_package_metadata")
        if pkg.get("forbidden") != ["canonical_record_write","canonical_chunk_write","embedding_index_write","source_pdf_write","external_api","taiwan_name_invention","layout_or_plate_self_approval"]: errs.append("package_forbidden_contract_drift")
        if [x.get("pdf_page") for x in pkg.get("source_locators",[])]!=pkg.get("pdf_pages"): errs.append("package_locator_coverage_drift")
        for loc in pkg.get("source_locators",[]):
            page=pages.get((pkg["source_id"],loc["pdf_page"]))
            if page is None or not (loc.get("source_pdf_sha256")==source_hash[pkg["source_id"]] and loc.get("char_start")==0 and loc.get("char_end")==len(page["text"]) and loc.get("page_text_sha256")==page["text_sha256"] and loc.get("exact_text_sha256")==th(page["text"])): errs.append(f"package_locator_drift:p{loc.get('pdf_page')}")
        if pkg["recovery_kind"]=="page_quality_with_terminal_no_text_exclusions":
            if len(pkg.get("ocr_exclusions",[]))!=1: errs.append("ocr_exclusion_cardinality_drift")
            for ex in pkg.get("ocr_exclusions",[]):
                op=root/ex["ocr_artifact"]; o=rj(op) if op.is_file() else {}
                if not (o.get("receipt_sha256")==default_receipt_hash(o)==ex.get("ocr_receipt_sha256") and o.get("output_sha256")==ex.get("ocr_output_sha256") and o.get("text")=="" and o.get("staging_disposition")=="no_text_detected" and o.get("terminal") is True and o.get("review_status")=="machine_extracted"): errs.append("ocr_terminal_no_text_chain_drift")
        elif pkg["recovery_kind"]=="terminal_body_boundary_split":
            b=pkg.get("terminal_boundary",{}); loc=b.get("boundary_source_locator",{}); page=pages.get((pkg["source_id"],b.get("trailing_plate_start_pdf_page")))
            if page is None or page["text"][loc.get("char_start",0):loc.get("char_end",0)]!=b.get("boundary_marker") or loc.get("exact_text_sha256")!=th(b.get("boundary_marker","")) or loc.get("page_text_sha256")!=page["text_sha256"]: errs.append("terminal_boundary_exact_marker_drift")
            # One recovered body may be split into several <=6-page packages.
            # The terminal boundary belongs to the parent body, not each child.
            if b.get("body_end_pdf_page")!=max(parent_body_pages.get(pkg["parent_entry_id"], set())): errs.append("terminal_body_end_drift")
        else: errs.append("unknown_recovery_kind")
        rp=root/"structure/recovery-maker-receipts"/f"{pid.replace(':','__')}.json"; receipt=rj(rp) if rp.is_file() else None
        locators=[]; repair_projection=None
        if receipt is None: status="awaiting_receipt"
        else:
            expected={"schema_version":"1.0","package_id":pid,"parent_entry_id":pkg["parent_entry_id"],"child_entry_id":None,"recovered_entry_id":pkg["recovered_entry_id"],"work_id":pkg["work_id"],"owner_shard":pkg["owner_shard"],"package_sha256":pkg["package_sha256"],"prompt_version":"plant-structure-continuation-line-anchors-v1","external_model_calls":0,"incremental_usd":0,"name_resolution_status":"unresolved","layout_or_plate_claims_approved":False,"deterministic_status":"pass","errors":[]}
            if any(receipt.get(k)!=v for k,v in expected.items()) or receipt.get("receipt_sha256")!=oh(receipt,"receipt_sha256"): errs.append("receipt_identity_hash_or_safety_drift")
            d=receipt.get("draft",{}); expected_d={"package_id":pid,"parent_entry_id":pkg["parent_entry_id"],"display_name":None,"name_resolution":{"status":"unresolved","sources":[]},"review_status":"machine_extracted"}
            if any(d.get(k)!=v for k,v in expected_d.items()) or d.get("book_taxon",{}).get("scientific_name_candidate")!=pkg["book_taxon_candidate"]: errs.append("draft_identity_name_or_taxon_drift")
            sections=d.get("sections",[])
            if not sections or len(sections)>6: errs.append("draft_sections_invalid")
            seen=set()
            for i,s in enumerate(sections):
                page=pages.get((pkg["source_id"],s.get("pdf_page"))); lr=s.get("source_line_range")
                if page is None or s.get("pdf_page") not in pkg["pdf_pages"] or not isinstance(lr,list) or len(lr)!=2: errs.append(f"section_{i}_locator_invalid"); continue
                st,en=lr; lines=page["text"].splitlines()
                if not all(isinstance(x,int) for x in lr) or st<1 or en<st or en>len(lines): errs.append(f"section_{i}_range_invalid"); continue
                q="\n".join(lines[st-1:en]); cs=page["text"].find(q)
                if not q.strip() or cs<0 or page["text"].find(q,cs+1)>=0 or s.get("exact_source_quote")!=q: errs.append(f"section_{i}_quote_invalid"); continue
                if s.get("section_type") in seen: errs.append(f"section_{i}_duplicate_type")
                seen.add(s.get("section_type"))
                locators.append({"source_id":pkg["source_id"],"volume":pkg["volume"],"pdf_page":s["pdf_page"],"source_pdf_sha256":source_hash[pkg["source_id"]],"char_start":cs,"char_end":cs+len(q),"source_line_start":st,"source_line_end":en,"page_text_sha256":page["text_sha256"],"exact_text_sha256":th(q),"section_index":i,"section_type":s.get("section_type")})
            if receipt.get("section_source_locators")!=locators: errs.append("receipt_locator_projection_drift")
            rep=receipt.get("changed_strategy_repair")
            if rep is not None:
                if not (rep.get("strategy")=="drop-unmaterialized-sections-v1" and rep.get("external_model_calls")==0 and rep.get("content_added") is False and rep.get("line_numbers_guessed_or_clamped") is False and receipt.get("model")=="deterministic-local-repair-no-model-call"): errs.append("unsafe_changed_strategy_repair")
                prior=root/"structure/recovery-attempts"/pid.replace(":","__")/f"prior-receipt-{rep.get('source_receipt_sha256')}.json"; pv=rj(prior) if prior.is_file() else {}
                attempt=root/"structure/recovery-attempts"/pid.replace(":","__")/f"attempt-{receipt.get('attempt_sha256')}.json"; av=rj(attempt) if attempt.is_file() else {}
                if pv.get("receipt_sha256")!=oh(pv,"receipt_sha256") or pv.get("receipt_sha256")!=rep.get("source_receipt_sha256") or pv.get("errors")!=rep.get("source_errors"): errs.append("repair_prior_receipt_chain_drift")
                if av.get("attempt_sha256")!=oh(av,"attempt_sha256") or av.get("attempt_sha256")!=receipt.get("attempt_sha256") or av.get("source_receipt_sha256")!=rep.get("source_receipt_sha256") or av.get("source_attempt_sha256")!=rep.get("source_attempt_sha256"): errs.append("repair_attempt_chain_drift")
                if not (av.get("package_id")==pid and av.get("package_sha256")==pkg["package_sha256"] and av.get("model")=="deterministic-local-repair-no-model-call" and av.get("repair_strategy")==rep.get("strategy") and av.get("dropped_section_indexes")==rep.get("dropped_section_indexes") and av.get("source_errors")==rep.get("source_errors") and av.get("parse_or_validation_errors")==[]): errs.append("repair_attempt_projection_drift")
                error_indexes=[]
                for error in rep.get("source_errors",[]):
                    if isinstance(error,str) and error.startswith("section_"):
                        try: error_indexes.append(int(error.split(":",1)[0].split("_",1)[1]))
                        except ValueError: pass
                if sorted(set(error_indexes))!=sorted(rep.get("dropped_section_indexes",[])): errs.append("repair_drop_index_source_error_drift")
                repair_projection=rep
            status="pass" if not errs else "needs_review"
        check={"schema_version":"2.0","package_id":pid,"parent_entry_id":pkg["parent_entry_id"],"recovered_entry_id":pkg["recovered_entry_id"],"package_sha256":pkg["package_sha256"],"receipt_path":str(rp.relative_to(root)) if receipt else None,"receipt_sha256":receipt.get("receipt_sha256") if receipt else None,"status":status,"errors":sorted(set(errs)),"section_source_locators":locators,"changed_strategy_repair":repair_projection,"checked_at":now(),"safety":{"canonical_writes":False,"chunk_writes":False,"index_writes":False,"embedding_calls":False,"external_api_calls":False,"taiwan_name_resolution":False,"layout_or_plate_approval":False}}
        check["check_sha256"]=oh(check,"check_sha256"); checks.append(check)
        if status=="pass":
            safe=[]; excluded=0
            locator_by_index={x["section_index"]:x for x in locators}
            for i,s in enumerate(receipt["draft"]["sections"]):
                if s["section_type"]=="plate_description": excluded+=1; continue
                loc=locator_by_index.get(i)
                if loc is None:
                    check["status"]="needs_review"; check["errors"]=["candidate_locator_projection_missing"]
                    continue
                safe.append({"section_type":s["section_type"],"exact_source_quotes":[s["exact_source_quote"]],"normalized_text_candidate":s.get("normalized_text_candidate"),"zh_tw_rendering_candidate":s.get("zh_tw_rendering_candidate"),"source_locators":[loc]})
            if not safe: check["status"]="needs_review"; check["errors"]=["no_nonplate_sections"]; check["check_sha256"]=oh(check,"check_sha256")
            else:
                c={"schema_version":"2.0","entry_id":pkg["recovered_entry_id"],"source_parent_entry_id":pkg["parent_entry_id"],"source_id":pkg["source_id"],"volume":pkg["volume"],"book_taxon_candidate":pkg["book_taxon_candidate"],"recovery_kind":pkg["recovery_kind"],"package_sha256":pkg["package_sha256"],"receipt_sha256":receipt["receipt_sha256"],"validation_check_sha256":check["check_sha256"],"display_name":None,"name_resolution":{"status":"unresolved","sources":[]},"review_status":"machine_extracted","sections":safe,"excluded_plate_section_count":excluded,"layout_or_plate_claims_approved":False,"canonical_write_allowed":False,"embedding_call_performed":False}; c["candidate_sha256"]=oh(c,"candidate_sha256"); candidates.append(c)
    passed=sum(x["status"]=="pass" for x in checks); complete=not global_errors and passed==9 and len(candidates)==9
    if not complete: candidates=[]
    wjl(root/"checks/recovery-v2-validation.jsonl",checks)
    manifest={"schema_version":"2.0","pipeline_id":"preembedding-recovery-v2","source_receipt_sha256":hashlib.sha256(source_path.read_bytes()).hexdigest(),"all_packages_deterministic_pass":complete,"candidate_count":len(candidates),"candidates":candidates,"canonical_write_allowed":False,"chunk_write_allowed":False,"index_write_allowed":False,"embedding_calls_performed":False}; manifest["manifest_sha256"]=oh(manifest,"manifest_sha256"); wj(root/"integration-v2/recovery/embedding-ready-recovery-candidate-manifest.json",manifest)
    summary={"schema_version":"2.0","checked_at":now(),"packages":len(packages),"package_checks_passed":passed,"package_checks_needs_review":sum(x["status"]=="needs_review" for x in checks),"package_checks_awaiting_receipt":sum(x["status"]=="awaiting_receipt" for x in checks),"recovery_candidates":len(candidates),"complete":complete,"global_errors":global_errors,"safety":{"canonical_writes":False,"chunk_writes":False,"index_writes":False,"embedding_calls":False,"external_api_calls":False,"taiwan_name_resolution":False,"layout_or_plate_approval":False}}; summary["summary_sha256"]=oh(summary,"summary_sha256"); wj(root/"checks/recovery-v2-summary.json",summary); print(json.dumps(summary,ensure_ascii=False))
    if a.require_complete and not complete: raise SystemExit(1)
if __name__=="__main__": main()
