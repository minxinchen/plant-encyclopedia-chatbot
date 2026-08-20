#!/usr/bin/env python3
"""Fail-closed tests for the consolidated v2 staging validator."""
from __future__ import annotations
import hashlib,json,shutil,subprocess,tempfile
from pathlib import Path
from typing import Any
LAB=Path(__file__).resolve().parents[1];SRC=LAB/"data/candidates/preembedding-v1";VALIDATOR=LAB/"scripts/validate-consolidated-v2-staging-manifest.py"
def cj(v:Any)->str:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def oh(v:dict,f:str)->str:return hashlib.sha256(cj({k:x for k,x in v.items() if k!=f}).encode()).hexdigest()
def main()->None:
 results=[]
 for label in ("duplicate-entry-id","name-injection","source-locator-drift","old-parent-reintroduced"):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/"root";root.mkdir()
   for name in ("integration","structure","shards"):(root/name).symlink_to(SRC/name)
   shutil.copytree(SRC/"integration-v2",root/"integration-v2")
   p=root/"integration-v2/consolidated-embedding-ready-candidate-manifest.json";m=json.loads(p.read_text())
   if label=="duplicate-entry-id":m["candidates"][1]["entry_id"]=m["candidates"][0]["entry_id"];m["candidates"][1]["candidate_sha256"]=oh(m["candidates"][1],"candidate_sha256")
   elif label=="name-injection":m["candidates"][0]["display_name"]="未經核准名稱";m["candidates"][0]["candidate_sha256"]=oh(m["candidates"][0],"candidate_sha256")
   elif label=="source-locator-drift":m["candidates"][0]["sections"][0]["source_locators"][0]["char_end"]-=1;m["candidates"][0]["candidate_sha256"]=oh(m["candidates"][0],"candidate_sha256")
   else:
    old=json.loads((SRC/"integration/embedding-ready-candidate-manifest.json").read_text());parent=next(x for x in old["candidates"] if x["entry_id"]=="kohler-volume-1:p0209-p0222");m["candidates"].append(parent);m["candidate_count"]+=1
   m["manifest_sha256"]=oh(m,"manifest_sha256");p.write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n")
   r=subprocess.run(["python3",str(VALIDATOR),"--root",str(root),"--require-complete"],capture_output=True,text=True)
   if r.returncode==0:raise AssertionError(f"{label}: validator unexpectedly passed")
   results.append({"case":label,"status":"PASS_FAIL_CLOSED"})
 print(json.dumps({"status":"PASS","cases":results},ensure_ascii=False))
if __name__=="__main__":main()
