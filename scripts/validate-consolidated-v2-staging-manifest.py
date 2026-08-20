#!/usr/bin/env python3
"""Independent exact-source and safety validator for consolidated v2 staging."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
from typing import Any
LAB=Path(__file__).resolve().parents[1];ROOT=LAB/"data/candidates/preembedding-v1";ALLOWED={"structure_validated_text_candidate","structure_validated_text_candidate_plate_claims_held"}
def cj(v:Any)->str:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def h(v:Any)->str:return hashlib.sha256(cj(v).encode()).hexdigest()
def oh(v:dict,f:str)->str:return h({k:x for k,x in v.items() if k!=f})
def th(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def rj(p:Path)->dict:return json.loads(p.read_text())
def rjl(p:Path)->list[dict]:return [json.loads(x) for x in p.read_text().splitlines() if x]
def main()->None:
 ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,default=ROOT);ap.add_argument("--require-complete",action="store_true");a=ap.parse_args();root=a.root;errors=[]
 manifest=rj(root/"integration-v2/consolidated-embedding-ready-candidate-manifest.json");old=rj(root/"integration/embedding-ready-candidate-manifest.json");cont=rj(root/"integration-v2/embedding-ready-child-candidate-manifest.json");rec=rj(root/"integration-v2/recovery/embedding-ready-recovery-candidate-manifest.json");disp={x["entry_id"]:x for x in rjl(root/"integration/entry-dispositions.jsonl")};pages={}
 for path in (root/"shards").glob("S*/inputs/pages.jsonl"):
  for page in rjl(path):pages[(page["source_id"],page["pdf_page"])]=page
 cont_parents={x["source_parent_entry_id"] for x in cont["candidates"]};rec_parents={x["parent_entry_id"] for x in rjl(root/"structure/content-recovery-work-packages.jsonl")};regular=[x for x in old["candidates"] if disp.get(x["entry_id"],{}).get("terminal_disposition") in ALLOWED and x["entry_id"] not in cont_parents|rec_parents];expected=regular+cont["candidates"]+rec["candidates"]
 if manifest.get("manifest_sha256")!=oh(manifest,"manifest_sha256"):errors.append("manifest_hash")
 if manifest.get("candidates")!=expected:errors.append("candidate_order_or_projection")
 if manifest.get("breakdown")!={"regular_primary_candidates":231,"continuation_v2_child_candidates":34,"recovery_v2_candidates":9}:errors.append("breakdown")
 if manifest.get("candidate_count")!=274 or len({x.get("entry_id") for x in manifest.get("candidates",[])})!=274:errors.append("identity_cardinality")
 if manifest.get("unresolved_content_holds")!=0 or manifest.get("status")!="embedding_ready_staging":errors.append("completion_state")
 if any(manifest.get(k) is not False for k in ("canonical_write_allowed","chunk_write_allowed","index_write_allowed","embedding_calls_performed")):errors.append("manifest_write_safety")
 for candidate in manifest.get("candidates",[]):
  eid=candidate.get("entry_id")
  if candidate.get("candidate_sha256")!=oh(candidate,"candidate_sha256"):errors.append(f"{eid}:candidate_hash")
  if candidate.get("display_name") is not None or candidate.get("name_resolution")!={"status":"unresolved","sources":[]}:errors.append(f"{eid}:name_safety")
  if candidate.get("layout_or_plate_claims_approved") is not False or candidate.get("canonical_write_allowed") is not False or candidate.get("embedding_call_performed") is not False:errors.append(f"{eid}:layout_write_safety")
  sections=candidate.get("sections",[])
  if not sections or any(x.get("section_type")=="plate_description" for x in sections):errors.append(f"{eid}:plate_or_empty")
  for si,section in enumerate(sections):
   quotes=section.get("exact_source_quotes",[]);locs=section.get("source_locators",[])
   if len(quotes)!=len(locs) or not quotes:errors.append(f"{eid}:section_{si}:quote_locator_count");continue
   for qi,(quote,loc) in enumerate(zip(quotes,locs)):
    page=pages.get((loc.get("source_id"),loc.get("pdf_page")));start=loc.get("char_start");end=loc.get("char_end")
    if page is None or not isinstance(start,int) or not isinstance(end,int) or start<0 or end<start or end>len(page["text"]):errors.append(f"{eid}:section_{si}:{qi}:locator_bounds");continue
    if page["text"][start:end]!=quote or loc.get("page_text_sha256")!=page["text_sha256"] or loc.get("exact_text_sha256")!=th(quote):errors.append(f"{eid}:section_{si}:{qi}:exact_source")
 result={"status":"PASS" if not errors else "FAIL","complete":not errors,"regular_primary_candidates":len(regular),"continuation_v2_child_candidates":len(cont["candidates"]),"recovery_v2_candidates":len(rec["candidates"]),"total_candidates":manifest.get("candidate_count"),"unique_entry_ids":len({x.get("entry_id") for x in manifest.get("candidates",[])}),"unresolved_content_holds":manifest.get("unresolved_content_holds"),"errors":errors};print(json.dumps(result,ensure_ascii=False))
 if a.require_complete and errors:raise SystemExit(1)
if __name__=="__main__":main()
