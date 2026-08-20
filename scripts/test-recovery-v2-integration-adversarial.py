#!/usr/bin/env python3
"""Ensure recovery-v2 validation fails closed on representative tampering."""
from __future__ import annotations
import hashlib,json,shutil,subprocess,tempfile
from pathlib import Path
from typing import Any
LAB=Path(__file__).resolve().parents[1];SRC=LAB/"data/candidates/preembedding-v1";VALIDATOR=LAB/"scripts/validate-recovery-v2-integration.py"
def cj(v:Any)->str:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def oh(v:dict,f:str)->str:return hashlib.sha256(cj({k:x for k,x in v.items() if k!=f}).encode()).hexdigest()
def setup(base:Path,copy_structure:bool=False)->Path:
 root=base/"root";root.mkdir()
 for name in ("source-receipt.json","shards","checks"):(root/name).symlink_to(SRC/name)
 if copy_structure:shutil.copytree(SRC/"structure",root/"structure")
 else:(root/"structure").symlink_to(SRC/"structure")
 shutil.copytree(SRC/"integration-v2",root/"integration-v2");return root
def must_fail(root:Path,label:str)->None:
 p=subprocess.run(["python3",str(VALIDATOR),"--root",str(root),"--require-complete"],capture_output=True,text=True)
 if p.returncode==0:raise AssertionError(f"{label}: validator unexpectedly passed")
def main()->None:
 results=[]
 for label in ("taiwan-name-injection","plate-injection","source-locator-drift","repair-chain-tamper"):
  with tempfile.TemporaryDirectory() as td:
   root=setup(Path(td),copy_structure=label=="repair-chain-tamper")
   mp=root/"integration-v2/recovery/embedding-ready-recovery-candidate-manifest.json";m=json.loads(mp.read_text())
   if label=="taiwan-name-injection":m["candidates"][0]["display_name"]="杜撰臺灣名";m["candidates"][0]["candidate_sha256"]=oh(m["candidates"][0],"candidate_sha256")
   elif label=="plate-injection":m["candidates"][0]["sections"][0]["section_type"]="plate_description";m["candidates"][0]["candidate_sha256"]=oh(m["candidates"][0],"candidate_sha256")
   elif label=="source-locator-drift":m["candidates"][0]["sections"][0]["source_locators"][0]["char_start"]+=1;m["candidates"][0]["candidate_sha256"]=oh(m["candidates"][0],"candidate_sha256")
   else:
    paths=list((root/"structure/recovery-attempts").glob("*/attempt-*.json"));p=paths[0];o=json.loads(p.read_text());o["dropped_section_indexes"]=[];o["attempt_sha256"]=oh(o,"attempt_sha256");p.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n")
   if label!="repair-chain-tamper":m["manifest_sha256"]=oh(m,"manifest_sha256");mp.write_text(json.dumps(m,ensure_ascii=False,indent=2)+"\n")
   must_fail(root,label);results.append({"case":label,"status":"PASS_FAIL_CLOSED"})
 print(json.dumps({"status":"PASS","cases":results},ensure_ascii=False))
if __name__=="__main__":main()
