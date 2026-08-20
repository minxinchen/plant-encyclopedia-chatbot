#!/usr/bin/env python3
"""Independently validate the isolated recovery-v2 projection."""

from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any

LAB=Path(__file__).resolve().parents[1]
ROOT=LAB/"data/candidates/preembedding-v1"
def cj(v:Any)->str:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def h(v:Any)->str:return hashlib.sha256(cj(v).encode()).hexdigest()
def oh(v:dict,f:str)->str:return h({k:x for k,x in v.items() if k!=f})
def th(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def rj(p:Path)->dict:return json.loads(p.read_text())
def rjl(p:Path)->list[dict]:return [json.loads(x) for x in p.read_text().splitlines() if x]

def main()->None:
    ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,default=ROOT);ap.add_argument("--require-complete",action="store_true");a=ap.parse_args();root=a.root
    errors=[]; packages=rjl(root/"structure/content-recovery-work-packages.jsonl"); checks=rjl(root/"checks/recovery-v2-validation.jsonl"); manifest=rj(root/"integration-v2/recovery/embedding-ready-recovery-candidate-manifest.json")
    source=rj(root/"source-receipt.json"); source_hash={x["source_id"]:x["sha256"] for x in source["sources"]}; pages={}
    for path in (root/"shards").glob("S*/inputs/pages.jsonl"):
        for page in rjl(path): pages[(page["source_id"],page["pdf_page"])]=page
    if len(packages)!=9 or len(checks)!=9:errors.append("recovery_cardinality_drift")
    pmap={x["package_id"]:x for x in packages}; cmap={x["package_id"]:x for x in checks}
    if len(pmap)!=9 or len(cmap)!=9 or set(pmap)!=set(cmap):errors.append("package_check_coverage_drift")
    expected_candidates=[]
    for pid,pkg in pmap.items():
        check=cmap.get(pid,{})
        if pkg.get("package_sha256")!=oh(pkg,"package_sha256"):errors.append(f"{pid}:package_hash")
        if check.get("check_sha256")!=oh(check,"check_sha256") or check.get("status")!="pass" or check.get("errors")!=[]:errors.append(f"{pid}:check_not_pass")
        rp=root/"structure/recovery-maker-receipts"/f"{pid.replace(':','__')}.json"; receipt=rj(rp) if rp.is_file() else {}
        if receipt.get("receipt_sha256")!=oh(receipt,"receipt_sha256") or receipt.get("package_sha256")!=pkg.get("package_sha256") or receipt.get("deterministic_status")!="pass":errors.append(f"{pid}:receipt_chain")
        if receipt.get("name_resolution_status")!="unresolved" or receipt.get("layout_or_plate_claims_approved") is not False or receipt.get("external_model_calls")!=0:errors.append(f"{pid}:receipt_safety")
        draft=receipt.get("draft",{}); locators=[]; safe=[]; seen=set()
        if draft.get("display_name") is not None or draft.get("name_resolution")!={"status":"unresolved","sources":[]}:errors.append(f"{pid}:name_injection")
        for i,section in enumerate(draft.get("sections",[])):
            page=pages.get((pkg["source_id"],section.get("pdf_page"))); lr=section.get("source_line_range",[])
            if page is None or len(lr)!=2 or not all(isinstance(x,int) for x in lr) or lr[0]<1 or lr[1]<lr[0] or lr[1]>len(page["text"].splitlines()):errors.append(f"{pid}:section_{i}_range");continue
            quote="\n".join(page["text"].splitlines()[lr[0]-1:lr[1]]); start=page["text"].find(quote)
            if quote!=section.get("exact_source_quote") or start<0 or page["text"].find(quote,start+1)>=0:errors.append(f"{pid}:section_{i}_quote");continue
            if section.get("section_type") in seen:errors.append(f"{pid}:duplicate_section_type")
            seen.add(section.get("section_type")); loc={"source_id":pkg["source_id"],"volume":pkg["volume"],"pdf_page":section["pdf_page"],"source_pdf_sha256":source_hash[pkg["source_id"]],"char_start":start,"char_end":start+len(quote),"source_line_start":lr[0],"source_line_end":lr[1],"page_text_sha256":page["text_sha256"],"exact_text_sha256":th(quote),"section_index":i,"section_type":section.get("section_type")};locators.append(loc)
            if section.get("section_type")!="plate_description":safe.append({"section_type":section["section_type"],"exact_source_quotes":[quote],"normalized_text_candidate":section.get("normalized_text_candidate"),"zh_tw_rendering_candidate":section.get("zh_tw_rendering_candidate"),"source_locators":[loc]})
        if receipt.get("section_source_locators")!=locators or check.get("section_source_locators")!=locators:errors.append(f"{pid}:locator_projection")
        rep=receipt.get("changed_strategy_repair")
        if rep:
            if rep.get("strategy")!="drop-unmaterialized-sections-v1" or rep.get("content_added") is not False or rep.get("line_numbers_guessed_or_clamped") is not False or rep.get("external_model_calls")!=0:errors.append(f"{pid}:repair_safety")
            d=root/"structure/recovery-attempts"/pid.replace(":","__"); prior=d/f"prior-receipt-{rep.get('source_receipt_sha256')}.json"; attempt=d/f"attempt-{receipt.get('attempt_sha256')}.json"
            pv=rj(prior) if prior.is_file() else {}; av=rj(attempt) if attempt.is_file() else {}
            if pv.get("receipt_sha256")!=oh(pv,"receipt_sha256") or pv.get("errors")!=rep.get("source_errors"):errors.append(f"{pid}:repair_prior_chain")
            if av.get("attempt_sha256")!=oh(av,"attempt_sha256") or av.get("repair_strategy")!=rep.get("strategy") or av.get("dropped_section_indexes")!=rep.get("dropped_section_indexes") or av.get("source_errors")!=rep.get("source_errors"):errors.append(f"{pid}:repair_attempt_chain")
        expected={"schema_version":"2.0","entry_id":pkg["recovered_entry_id"],"source_parent_entry_id":pkg["parent_entry_id"],"source_id":pkg["source_id"],"volume":pkg["volume"],"book_taxon_candidate":pkg["book_taxon_candidate"],"recovery_kind":pkg["recovery_kind"],"package_sha256":pkg["package_sha256"],"receipt_sha256":receipt.get("receipt_sha256"),"validation_check_sha256":check.get("check_sha256"),"display_name":None,"name_resolution":{"status":"unresolved","sources":[]},"review_status":"machine_extracted","sections":safe,"excluded_plate_section_count":sum(x.get("section_type")=="plate_description" for x in draft.get("sections",[])),"layout_or_plate_claims_approved":False,"canonical_write_allowed":False,"embedding_call_performed":False};expected["candidate_sha256"]=oh(expected,"candidate_sha256");expected_candidates.append(expected)
    if manifest.get("manifest_sha256")!=oh(manifest,"manifest_sha256"):errors.append("manifest_hash")
    if manifest.get("candidates")!=expected_candidates or manifest.get("candidate_count")!=9 or manifest.get("all_packages_deterministic_pass") is not True:errors.append("manifest_candidate_projection")
    if any(manifest.get(k) is not False for k in ("canonical_write_allowed","chunk_write_allowed","index_write_allowed","embedding_calls_performed")):errors.append("manifest_write_safety")
    result={"status":"PASS" if not errors else "FAIL","complete":not errors,"packages":len(packages),"checks_passed":sum(x.get("status")=="pass" for x in checks),"recovery_candidates":manifest.get("candidate_count"),"errors":errors};print(json.dumps(result,ensure_ascii=False))
    if a.require_complete and errors:raise SystemExit(1)
if __name__=="__main__":main()
