#!/usr/bin/env python3
"""Renderiza SO os videos de um dia do plano semanal.
Uso: python3 produz_dia.py plano_semana_AAAA-MM-DD.json AAAA-MM-DD pasta_saida
Gera pasta_saida/resultado_AAAA-MM-DD.json com arquivo, horario, vencedor e legenda de cada video."""
import json, sys, os, subprocess, random
plan_path, day, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(outdir, exist_ok=True)
here = os.path.dirname(os.path.abspath(__file__))
items = [i for i in json.load(open(plan_path)) if i["date"] == day]
rp = os.path.join(outdir, "resultado_%s.json" % day)
results = json.load(open(rp)) if os.path.exists(rp) else {}
for it in items:
    name = "%s_%s_%s_%s.mp4" % (it["id"], day, it["time"].replace(":", "h"), it["mode"])
    out = os.path.join(outdir, name)
    if it["id"] in results and os.path.exists(out):
        continue
    seed = it.get("seed") or random.randint(1, 10**6)
    cmd = [sys.executable, os.path.join(here, "arena_engine.py"), "--mode", it["mode"], "--shapes", it.get("shapes", "times"),
           "--seed", str(seed), "--out", out, "--title", it["title"]]
    if it.get("question"):
        cmd += ["--question", it["question"]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ERRO", it["id"], r.stderr[-600:], flush=True)
        continue
    res = json.loads(r.stdout.strip().splitlines()[-1])
    res.update({"file": out, "time": it["time"], "date": day, "caption": it["caption"], "mode": it["mode"], "seed": seed})
    results[it["id"]] = res
    json.dump(results, open(rp, "w"), ensure_ascii=False, indent=1)
    print("ok", it["id"], json.dumps(res, ensure_ascii=False), flush=True)
