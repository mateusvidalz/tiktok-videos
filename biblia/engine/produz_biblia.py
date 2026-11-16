#!/usr/bin/env python3
"""Pipeline do canal "Sabia que na Biblia".

Subcomandos:
  prompts  roteiro.json            -> imprime os prompts das grades 2x2 (1 por 4 cenas), em JSON
  tts      roteiro.json            -> imprime o texto da locucao (para a ElevenLabs)
  montar   roteiro.json voz.mp3 paineis_dir saida.mp4 [--dur 70]
           acelera/ajusta a voz para caber em DUR, acha as pausas, alinha as frases,
           monta o spec e renderiza com biblia_engine.py (mesma pasta).

roteiro.json:
{
  "id": "2026-11-16-1",
  "legenda": "O homem que não morreu",          # ate 4 palavras (post)
  "hook": "existe um homem\\nque nunca morreu?",  # texto grande da abertura
  "personagem": "Enoch, an elderly man with a long grey beard, sand robe, olive-green mantle, wooden staff",
  "frases": [
    {"fala": "Você sabia que na Bíblia existe um homem que nunca morreu?", "tipo": "hook"},
    {"fala": "O nome dele era Enoque."},
    {"fala": "Em Gênesis, capítulo cinco, está escrito:"},
    {"fala": "E andou Enoque com Deus...", "tipo": "verso", "verso": "E andou Enoque com Deus; ...", "ref": "Gênesis 5:24"},
    {"fala": "Ele viveu trezentos e sessenta e cinco anos.", "tela": "Ele viveu 365 anos.", "destaque": "365 anos"},
    {"fala": "Comenta aqui se você já conhecia essa história.", "tipo": "cta"}
  ],
  "cenas": ["descricao em ingles da cena 1", ... 12 cenas],
  "fx": [["stars","dust"], ...]   # opcional, 1 por cena
  "end_ref": "Gênesis 5:18-27",
  "capa_titulo": "O homem que nunca morreu",   # titulo da capa (3 a 7 palavras)
  "capa_cena": 0                               # indice do quadro usado na capa
}
A capa vira o cartao final do video (ultimos ~2s) e e salva como <saida>_capa.png.
No Metricool use videoCoverMilliseconds = (DUR - 0.4) * 1000 (69600 para 70s).
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STYLE = ("hand-painted gouache illustration style, vintage storybook look, soft dry-brush texture, visible paper grain, "
         "muted palette of sand beige, ochre gold, sage and deep forest green, dusty blue, small terracotta accents")


def load(p):
    return json.load(open(p, encoding="utf-8"))


def cmd_prompts(r):
    cenas = r["cenas"]
    pers = r.get("personagem", "")
    out = []
    for g in range(0, len(cenas), 4):
        quad = cenas[g:g + 4]
        while len(quad) < 4:
            quad.append(quad[-1])
        pos = ["top left", "top right", "bottom left", "bottom right"]
        txt = ("A storyboard sheet: one image divided into a 2x2 grid of four equal vertical panels (each panel 3:4 portrait), "
               "separated by thin plain cream paper gutters (#F8EBD4). All four panels share the same " + STYLE + ". "
               "Each panel is a full-bleed painted scene filling its frame edge to edge, rich detail, main subject large in the frame.")
        if pers:
            txt += " The same characters appear consistently across panels: " + pers + "."
        for i, c in enumerate(quad):
            txt += f"\nPanel {i + 1} ({pos[i]}): {c}"
        txt += "\nNo text, no numbers, no captions, no borders other than the cream gutters."
        out.append(txt)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_tts(r):
    print("\n\n".join(f["fala"] for f in r["frases"]))


def dur_of(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())


def speech_segments(p):
    log = subprocess.run(["ffmpeg", "-i", p, "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", log)]
    en = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", log)]
    total = dur_of(p)
    sil = []
    for i, s in enumerate(st):
        e = en[i] if i < len(en) else total
        sil.append((s, e))
    # fala = complemento dos silencios
    segs, cur = [], 0.0
    for s, e in sil:
        if s - cur > 0.08:
            segs.append([cur, s])
        cur = e
    if total - cur > 0.08:
        segs.append([cur, total])
    return segs, total


def align(segs, n, weights):
    """Agrupa os trechos de fala em n frases (programacao dinamica): duracao de cada bloco
    proporcional ao tamanho do texto, preferindo cortar nas pausas mais longas."""
    k = len(segs)
    if k <= n:
        t0, t1 = segs[0][0], segs[-1][1]
        tot = sum(weights)
        out, acc = [], t0
        for w in weights:
            dd = (t1 - t0) * w / tot
            out.append([acc, acc + dd]); acc += dd
        return out
    T = segs[-1][1] - segs[0][0]
    W = float(sum(weights))
    exp = [T * w / W for w in weights]
    gap = [segs[i + 1][0] - segs[i][1] for i in range(k - 1)] + [1.0]
    INF = 1e18
    # dp[i][j] = custo de cobrir trechos 0..j-1 com i frases
    dp = [[INF] * (k + 1) for _ in range(n + 1)]
    bk = [[0] * (k + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(i, k - (n - i) + 1):
            best, arg = INF, 0
            for s0 in range(i - 1, j):
                if dp[i - 1][s0] >= INF:
                    continue
                dur = segs[j - 1][1] - segs[s0][0]
                c = dp[i - 1][s0] + (dur - exp[i - 1]) ** 2 / max(0.5, exp[i - 1]) - 2.5 * gap[j - 1]
                if c < best:
                    best, arg = c, s0
            dp[i][j], bk[i][j] = best, arg
    cuts, j = [], k
    for i in range(n, 0, -1):
        s0 = bk[i][j]; cuts.append((s0, j)); j = s0
    cuts.reverse()
    return [[segs[a][0], segs[b - 1][1]] for a, b in cuts]


def cmd_montar(r, voz, pdir, saida, DUR=70.0):
    work = os.path.dirname(os.path.abspath(saida)) or "."
    raw = dur_of(voz)
    target = DUR - 3.0  # 0.5s de entrada + ~2.5s de respiro final
    tempo = max(1.0, min(1.25, raw / target))
    vr = os.path.join(work, r["id"] + "_voz.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", voz, "-af", f"atempo={tempo:.4f}", vr], check=True)
    segs, vdur = speech_segments(vr)
    fr = r["frases"]
    blocks = align(segs, len(fr), [max(8, len(f["fala"])) for f in fr])
    d = 0.5
    caps, verse, his, hook, cta = [], None, [], None, None
    for i, (f, (a, b)) in enumerate(zip(fr, blocks)):
        nxt = blocks[i + 1][0] if i + 1 < len(blocks) else b + 1.5
        b2 = min(b + 0.4, nxt - 0.08)
        t0, t1 = round(a + d - 0.1, 2), round(b2 + d, 2)
        tipo = f.get("tipo")
        if tipo == "hook":
            hook = {"t0": 0.3, "t1": max(2.5, t1), "text": r.get("hook", f["fala"])}
            continue
        if tipo == "verso":
            verse = {"t0": t0, "t1": t1 + 0.3, "text": f["verso"], "ref": f["ref"]}
            continue
        if tipo == "cta":
            cta = {"t0": t0, "t1": DUR - 0.3, "text": f.get("tela", f["fala"])}
            continue
        caps.append({"t0": t0, "t1": t1, "text": f.get("tela", f["fala"])})
        if f.get("destaque"):
            his.append({"t0": t0, "t1": t1 + 0.6, "text": f["destaque"]})
    # paineis
    pan = sorted(p for p in os.listdir(pdir) if p.lower().endswith(".png"))
    n = len(pan)
    fxs = r.get("fx") or []
    pans = [[0, -20], [30, 0], [0, -25], [-30, -10], [0, -20], [30, 0], [-25, -10], [0, -25]]
    zs = [[1.0, 1.08], [1.03, 1.10], [1.0, 1.10], [1.08, 1.0]]
    scenes, step = [], DUR / n
    for i, p in enumerate(pan):
        scenes.append({"img": os.path.join(os.path.abspath(pdir), p), "start": round(i * step, 2), "end": round((i + 1) * step, 2),
                       "zoom": zs[i % len(zs)], "pan": pans[i % len(pans)],
                       "fx": fxs[i] if i < len(fxs) else (["dust", "birds"] if i % 2 else ["dust"])})
    spec = {"audio": os.path.abspath(vr), "voice_delay": d, "duration": DUR, "seed": 5, "scenes": scenes, "captions": caps,
            "hook": hook, "verse": verse, "highlights": his, "end_ref": r.get("end_ref"), "cta": cta}
    ci = int(r.get("capa_cena", 0))
    spec["cover"] = {"img": scenes[min(ci, n - 1)]["img"], "title": r.get("capa_titulo") or r.get("legenda", "")}
    sp = os.path.join(work, r["id"] + "_spec.json")
    json.dump(spec, open(sp, "w"), ensure_ascii=False, indent=1)
    print(f"voz bruta {raw:.1f}s, tempo {tempo:.3f}, voz final {vdur:.1f}s, {len(segs)} trechos -> {len(fr)} frases, {n} cenas")
    subprocess.run([sys.executable, os.path.join(HERE, "biblia_engine.py"), sp, saida], check=True)


if __name__ == "__main__":
    c = sys.argv[1]
    r = load(sys.argv[2])
    if c == "prompts":
        cmd_prompts(r)
    elif c == "tts":
        cmd_tts(r)
    elif c == "montar":
        dur = float(sys.argv[sys.argv.index("--dur") + 1]) if "--dur" in sys.argv else 70.0
        cmd_montar(r, sys.argv[3], sys.argv[4], sys.argv[5], dur)
