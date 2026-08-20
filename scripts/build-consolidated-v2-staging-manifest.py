#!/usr/bin/env python3
"""Build a staging-only consolidated v2 candidate manifest."""
from __future__ import annotations
import argparse,hashlib,json,tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
LAB=Path(__file__).resolve().parents[1]; ROOT=LAB/"data/candidates/preembedding-v1"
ALLOWED={"structure_validated_text_candidate","structure_validated_text_candidate_plate_claims_held"}
def cj(v:Any)->str:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def h(v:Any)->str:return hashlib.sha256(cj(v).encode()).hexdigest()
def oh(v:dict,f:str)->str:return h({k:x for k,x in v.items() if k!=f})
def rj(p:Path)->dict:return json.loads(p.read_text())
def rjl(p:Path)->list[dict]:return [json.loads(x) for x in p.read_text().splitlines() if x]
def write(p:Path,v:dict)->None:
 p.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=p.parent,delete=False) as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write("\n");t=Path(f.name)
 t.replace(p)
def main()->None:
 ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,default=ROOT);ap.add_argument("--require-complete",action="store_true");a=ap.parse_args();root=a.root;errors=[]
 old=rj(root/"integration/embedding-ready-candidate-manifest.json"); cont=rj(root/"integration-v2/embedding-ready-child-candidate-manifest.json"); rec=rj(root/"integration-v2/recovery/embedding-ready-recovery-candidate-manifest.json"); dispositions={x["entry_id"]:x for x in rjl(root/"integration/entry-dispositions.jsonl")}
 cont_parents={x["source_parent_entry_id"] for x in cont.get("candidates",[])}; rec_parents={x["parent_entry_id"] for x in rjl(root/"structure/content-recovery-work-packages.jsonl")}
 if len(cont_parents)!=18 or len(rec_parents)!=8:errors.append("replacement_parent_cardinality_drift")
 regular=[]
 for c in old.get("candidates",[]):
  disp=dispositions.get(c.get("entry_id"),{}).get("terminal_disposition")
  if disp in ALLOWED and c.get("entry_id") not in cont_parents|rec_parents:regular.append(c)
 if len(regular)!=231:errors.append("regular_primary_count_drift")
 if old.get("manifest_sha256")!=oh(old,"manifest_sha256") or cont.get("manifest_sha256")!=oh(cont,"manifest_sha256") or rec.get("manifest_sha256")!=oh(rec,"manifest_sha256"):errors.append("upstream_manifest_hash_drift")
 if cont.get("candidate_count")!=34 or cont.get("all_packages_deterministic_pass") is not True:errors.append("continuation_v2_incomplete")
 if rec.get("candidate_count")!=9 or rec.get("all_packages_deterministic_pass") is not True:errors.append("recovery_v2_incomplete")
 candidates=regular+cont.get("candidates",[])+rec.get("candidates",[]); ids=[x.get("entry_id") for x in candidates]
 if len(candidates)!=274 or len(ids)!=len(set(ids)):errors.append("consolidated_identity_cardinality_drift")
 for c in candidates:
  if c.get("candidate_sha256")!=oh(c,"candidate_sha256"):errors.append(f"candidate_hash:{c.get('entry_id')}")
  if c.get("display_name") is not None or c.get("name_resolution")!={"status":"unresolved","sources":[]}:errors.append(f"name_safety:{c.get('entry_id')}")
  if c.get("layout_or_plate_claims_approved") is not False or c.get("canonical_write_allowed") is not False or c.get("embedding_call_performed") is not False:errors.append(f"write_layout_safety:{c.get('entry_id')}")
  if not c.get("sections") or any(s.get("section_type")=="plate_description" for s in c.get("sections",[])):errors.append(f"plate_or_empty_sections:{c.get('entry_id')}")
 complete=not errors
 if not complete:candidates=[]
 out={"schema_version":"2.0","pipeline_id":"preembedding-consolidated-v2-staging","generated_at":datetime.now().astimezone().isoformat(timespec="seconds"),"status":"embedding_ready_staging" if complete else "blocked","source_manifests":{"regular_primary_manifest_sha256":old.get("manifest_sha256"),"continuation_v2_manifest_sha256":cont.get("manifest_sha256"),"recovery_v2_manifest_sha256":rec.get("manifest_sha256")},"excluded_parent_sets":{"continuation_parent_count":len(cont_parents),"recovery_parent_count":len(rec_parents),"continuation_parent_entry_ids":sorted(cont_parents),"recovery_parent_entry_ids":sorted(rec_parents)},"breakdown":{"regular_primary_candidates":len(regular) if complete else 0,"continuation_v2_child_candidates":34 if complete else 0,"recovery_v2_candidates":9 if complete else 0},"candidate_count":len(candidates),"candidates":candidates,"entry_ids_unique":len(ids)==len(set(ids)),"unresolved_content_holds":0 if complete else 8,"canonical_write_allowed":False,"chunk_write_allowed":False,"index_write_allowed":False,"embedding_calls_performed":False,"errors":sorted(set(errors))};out["manifest_sha256"]=oh(out,"manifest_sha256");write(root/"integration-v2/consolidated-embedding-ready-candidate-manifest.json",out)
 summary={"schema_version":"2.0","status":"PASS" if complete else "FAIL","regular_primary_candidates":len(regular),"continuation_v2_child_candidates":cont.get("candidate_count",0),"recovery_v2_candidates":rec.get("candidate_count",0),"total_candidates":len(candidates),"unique_entry_ids":len(set(ids)),"excluded_continuation_parents":len(cont_parents),"excluded_recovery_parents":len(rec_parents),"unresolved_content_holds":0 if complete else 8,"errors":sorted(set(errors))};summary["summary_sha256"]=oh(summary,"summary_sha256");write(root/"checks/consolidated-v2-summary.json",summary);print(json.dumps(summary,ensure_ascii=False))
 if a.require_complete and not complete:raise SystemExit(1)
if __name__=="__main__":main()
