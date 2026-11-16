#!/usr/bin/env python3
"""Motor de video "Sabia que na Biblia" (TikTok 9:16, soft).

Uso: python3 biblia_engine.py spec.json saida.mp4 [--preview 2,15,30]

spec.json:
{
  "audio": "voz.mp3",            # locucao
  "duration": 50.0,              # duracao total (s)
  "scenes": [ {"img": "s1.png", "start": 0, "end": 6, "zoom": [1.0, 1.07], "pan": [0, -10], "fx": ["stars"|"rays"|"birds"|"dust"]} ],
  "captions": [ {"t0": 0.0, "t1": 3.2, "text": "..."} ],
  "hook": {"t0": 0, "t1": 5.4, "text": "existe um homem\nque nunca morreu?"},
  "verse": {"t0": 23.2, "t1": 28.3, "text": "...", "ref": "Gênesis 5:24"},
  "highlights": [ {"t0": 28.7, "t1": 33.7, "text": "365 anos"} ],
  "end_ref": "Gênesis 5:21-27",
  "cta": null | {"t0": 47, "t1": 50, "text": "..."}
}
"""
import json, math, random, subprocess, sys, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
CREAM = (248, 232, 206)
INK = (74, 58, 42)         # marrom tinta
OCHRE = (176, 132, 62)     # dourado ocre
SAGE = (92, 110, 84)
FONT_DIR = os.path.expanduser("~/.fonts")
IMG_TOP = 380             # topo da ilustracao (layout vertical cheio)
IMG_W = 1120
FADE_TOP = 90             # degradê no topo
FADE_BOT0, FADE_BOT1 = 1330, 1560  # degradê inferior (y absoluto)
TEXT_Y = 1610             # zona de texto


def font(name, size, var=None):
    f = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    if var:
        try:
            f.set_variation_by_name(var)
        except Exception:
            pass
    return f


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def fade_alpha(t, t0, t1, fin=0.45, fout=0.45):
    if t < t0 or t > t1:
        return 0.0
    return min(ease((t - t0) / fin), ease((t1 - t) / fout))


def wrap(draw, text, fnt, maxw):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=fnt) <= maxw:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def text_layer(text, fnt, color, maxw=900, spacing=1.18, tracking=0, shadow=False):
    """Renderiza bloco centralizado; retorna RGBA recortado."""
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    lines = wrap(d, text, fnt, maxw)
    asc, desc = fnt.getmetrics()
    lh = int((asc + desc) * spacing)
    widths = []
    for ln in lines:
        if tracking:
            widths.append(sum(d.textlength(c, font=fnt) for c in ln) + tracking * max(0, len(ln) - 1))
        else:
            widths.append(d.textlength(ln, font=fnt))
    bw = int(max(widths)) + 40
    bh = lh * len(lines) + 30
    im = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        x = (bw - widths[i]) / 2
        y = 12 + i * lh
        if tracking:
            for c in ln:
                d.text((x, y), c, font=fnt, fill=color)
                x += d.textlength(c, font=fnt) + tracking
        else:
            d.text((x, y), ln, font=fnt, fill=color)
    if shadow:
        glow = Image.new("RGBA", im.size, CREAM + (0,))
        a = im.split()[3].filter(ImageFilter.GaussianBlur(10))
        glow.putalpha(a.point(lambda v: min(255, int(v * 1.6))))
        glow = Image.composite(Image.new("RGBA", im.size, CREAM + (255,)), Image.new("RGBA", im.size, (0, 0, 0, 0)), glow.split()[3])
        glow.alpha_composite(im)
        im = glow
    return im


def paste_alpha(frame, layer, cx, cy, alpha):
    if alpha <= 0.01:
        return
    if alpha < 0.999:
        layer = layer.copy()
        a = layer.split()[3].point(lambda v: int(v * alpha))
        layer.putalpha(a)
    x = int(cx - layer.width / 2)
    y = int(cy - layer.height / 2)
    frame.alpha_composite(layer, (max(0, x), max(0, y)))


def edge_mask(w, h, feather=70):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rectangle([feather, feather, w - feather, h - feather], fill=255)
    return m.filter(ImageFilter.GaussianBlur(feather / 2))


def blend_to_cream(img):
    """Funde bordas da ilustracao no creme."""
    img = img.convert("RGB")
    bg = Image.new("RGB", img.size, CREAM)
    return Image.composite(img, bg, edge_mask(*img.size, feather=max(40, img.width // 22)))


class Scene:
    def __init__(self, cfg, rng):
        im = Image.open(cfg["img"]).convert("RGB")
        scale = IMG_W / im.width
        self.img = im.resize((IMG_W, int(im.height * scale)), Image.LANCZOS)
        self.t0, self.t1 = cfg["start"], cfg["end"]
        self.z0, self.z1 = cfg.get("zoom", [1.0, 1.06])
        self.pan = cfg.get("pan", [0, 0])
        self.fx = cfg.get("fx", ["dust"])
        self.rng = random.Random(rng.random())
        ih = self.img.height
        self.top = IMG_TOP
        self.cy = IMG_TOP + ih / 2
        # particulas de poeira dourada
        self.dust = [(self.rng.uniform(80, W - 80), self.rng.uniform(self.top + 40, self.top + ih - 40),
                      self.rng.uniform(1.5, 3.6), self.rng.uniform(0, 6.28), self.rng.uniform(6, 16)) for _ in range(26)]
        # passaros
        self.birds = [(self.rng.uniform(-0.3, 0.2), self.top + self.rng.uniform(40, ih * 0.35),
                       self.rng.uniform(0.9, 1.4), self.rng.uniform(0, 6.28)) for _ in range(3)]
        # estrelas: pontos claros no ceu escuro
        self.stars = []
        if "stars" in self.fx:
            a = np.asarray(self.img.convert("L")).astype(np.int16)
            hh = a.shape[0]
            ys, xs = np.where(a[: int(hh * 0.55)] > 200)
            dark = np.asarray(self.img.convert("L").filter(ImageFilter.BoxBlur(12)))
            cand = [(x, y) for x, y in zip(xs, ys) if dark[y, x] < 120]
            self.rng.shuffle(cand)
            for x, y in cand[:70]:
                self.stars.append((x, y + self.top, self.rng.uniform(0, 6.28), self.rng.uniform(0.8, 2.0)))

    def render(self, t):
        p = (t - self.t0) / max(0.1, (self.t1 - self.t0))
        p = max(0, min(1, p))
        z = self.z0 + (self.z1 - self.z0) * ease(p)
        frame = Image.new("RGBA", (W, H), CREAM + (255,))
        w, h = int(self.img.width * z), int(self.img.height * z)
        im = self.img.resize((w, h), Image.BILINEAR)
        x = int((W - w) / 2 + self.pan[0] * p)
        y = int(IMG_TOP + self.pan[1] * p - (h - self.img.height) * 0.35)
        frame.paste(im, (x, y))
        frame.alpha_composite(FADE_LAYER)
        d = ImageDraw.Draw(frame, "RGBA")
        if "rays" in self.fx:
            ray = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ray)
            cx, cy = W * 0.5, self.top - 40
            for k in range(7):
                ang = math.radians(70 + k * 6.5 + 2.5 * math.sin(t * 0.4 + k))
                L = 900
                a = int(26 + 16 * math.sin(t * 0.7 + k * 1.3))
                x2, y2 = cx + L * math.cos(ang) * (1 if k % 2 else -1) * 0.35, cy + L * math.sin(ang)
                rd.polygon([(cx - 6, cy), (cx + 6, cy), (x2 + 40, y2), (x2 - 40, y2)], fill=(255, 226, 150, a))
            ray = ray.filter(ImageFilter.GaussianBlur(18))
            frame.alpha_composite(ray)
        if self.stars:
            for sx, sy, ph, sp in self.stars:
                a = 0.5 + 0.5 * math.sin(t * sp + ph)
                r = 1.6 + 1.6 * a
                sxp = (sx - W / 2) * z + W / 2 + self.pan[0] * p
                syp = (sy - self.top) * z + self.top + self.pan[1] * p
                d.ellipse([sxp - r, syp - r, sxp + r, syp + r], fill=(255, 246, 214, int(120 + 120 * a)))
        if "birds" in self.fx:
            for bx0, by, sp, ph in self.birds:
                bx = (bx0 + (t - self.t0) * 0.035 * sp) * W
                if -40 < bx < W + 40:
                    flap = 7 * math.sin(t * 5 * sp + ph)
                    yy = by + 6 * math.sin(t * 0.8 + ph)
                    d.line([(bx - 14, yy - flap), (bx, yy), (bx + 14, yy - flap)], fill=(70, 62, 52, 200), width=3)
        if "dust" in self.fx:
            for dx, dy, r, ph, sp in self.dust:
                yy = dy - ((t * sp) % 120)
                xx = dx + 10 * math.sin(t * 0.5 + ph)
                a = int(70 + 60 * math.sin(t * 1.3 + ph))
                d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=(255, 232, 170, max(0, a)))
        return frame


def make_fade_layer():
    a = np.zeros((H, W), np.float32)
    y = np.arange(H, dtype=np.float32)
    top = np.clip(1 - (y - IMG_TOP) / FADE_TOP, 0, 1)
    top[y < IMG_TOP] = 1
    bot = np.clip((y - FADE_BOT0) / (FADE_BOT1 - FADE_BOT0), 0, 1)
    col = np.maximum(top, bot)
    col = col * col * (3 - 2 * col)
    a[:] = col[:, None]
    lay = np.zeros((H, W, 4), np.uint8)
    lay[..., :3] = CREAM
    lay[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(lay, "RGBA")


FADE_LAYER = None


def paper_grain():
    rng = np.random.default_rng(3)
    n = rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
    g = Image.fromarray(np.clip(128 + n * 10, 0, 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
    g = g.filter(ImageFilter.GaussianBlur(0.8))
    return (np.asarray(g).astype(np.float32) - 128) / 255.0


def build_music(dur, path):
    """Pad ambiente suave sintetizado (acordes lentos, sem batida)."""
    sr = 44100
    t = np.arange(int(sr * (dur + 1))) / sr
    chords = [[146.83, 220.0, 293.66, 369.99], [123.47, 185.0, 246.94, 293.66],
              [130.81, 196.0, 261.63, 329.63], [110.0, 164.81, 220.0, 277.18]]  # D, Bm, C, A (suaves)
    seg = 8.0
    out = np.zeros_like(t)
    for i in range(int(dur / seg) + 2):
        c = chords[i % len(chords)]
        s, e = i * seg - 2, (i + 1) * seg + 2
        m = (t >= s) & (t < e)
        tt = t[m] - s
        env = np.sin(np.pi * np.clip(tt / (e - s), 0, 1)) ** 2
        for f in c:
            for det in (-0.6, 0.6):
                out[m] += env * (np.sin(2 * np.pi * (f + det) * t[m]) + 0.25 * np.sin(4 * np.pi * (f + det) * t[m]))
    # brilho suave (sino) a cada 4s
    for k in np.arange(1.0, dur, 4.0):
        m = (t >= k) & (t < k + 3)
        tt = t[m] - k
        f = [587.33, 659.25, 739.99, 880.0][int(k) % 4]
        out[m] += 0.35 * np.exp(-tt * 1.8) * np.sin(2 * np.pi * f * t[m])
    out /= np.max(np.abs(out)) + 1e-9
    fade = np.clip(np.minimum(t / 2.0, (dur + 0.5 - t) / 2.5), 0, 1)
    out *= fade * 0.9
    import wave
    data = (out * 32767 * 0.8).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(data.tobytes())


def main():
    spec = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    preview = None
    if "--preview" in sys.argv:
        preview = [float(x) for x in sys.argv[sys.argv.index("--preview") + 1].split(",")]
    base = os.path.dirname(os.path.abspath(sys.argv[1]))
    os.chdir(base)
    dur = spec["duration"]
    rng = random.Random(spec.get("seed", 7))
    scenes = [Scene(s, rng) for s in spec["scenes"]]
    XF = 0.8  # crossfade

    f_label = font("Lora.ttf", 40, "SemiBold")
    f_hook = font("Cormorant.ttf", 100, "SemiBold")
    f_cap = font("Lora.ttf", 52, "Medium")
    f_verse = font("CormorantItalic.ttf", 74, "Medium")
    f_ref = font("Lora.ttf", 40, "SemiBold")
    f_hi = font("Cormorant.ttf", 150, "Bold")
    f_small = font("Lora.ttf", 36, "Medium")

    label = text_layer("VOCÊ SABIA QUE NA BÍBLIA...", f_label, OCHRE, maxw=1000, tracking=5)
    handle = text_layer(spec.get("handle", "@sabia_que_na_biblia"), font("Lora.ttf", 40, "SemiBold"), INK, maxw=1000)
    hook = text_layer(spec["hook"]["text"], f_hook, INK, maxw=920, spacing=1.0) if spec.get("hook") else None
    caps = [(c["t0"], c["t1"], text_layer(c["text"], f_cap, INK, maxw=900, spacing=1.2, shadow=False)) for c in spec["captions"]]
    verse = None
    if spec.get("verse"):
        v = spec["verse"]
        vt = text_layer("“" + v["text"] + "”", f_verse, INK, maxw=900, spacing=1.08, shadow=False)
        vr = text_layer(v["ref"].upper(), f_ref, OCHRE, maxw=900, tracking=4)
        verse = (v["t0"], v["t1"], vt, vr)
    his = [(h["t0"], h["t1"], text_layer(h["text"], f_hi, OCHRE, maxw=1000, spacing=1.0, shadow=False)) for h in spec.get("highlights", [])]
    endref = text_layer(spec["end_ref"], f_small, SAGE, maxw=900) if spec.get("end_ref") else None
    cta = None
    if spec.get("cta"):
        c = spec["cta"]
        cta = (c["t0"], c["t1"], text_layer(c["text"], f_cap, INK, maxw=880, spacing=1.2, shadow=False))
    global FADE_LAYER
    FADE_LAYER = make_fade_layer()
    grain = paper_grain()
    rule = Image.new("RGBA", (160, 4), OCHRE + (200,))

    def render(t):
        # cenas com crossfade
        active = [s for s in scenes if s.t0 - XF / 2 <= t <= s.t1 + XF / 2]
        if not active:
            active = [min(scenes, key=lambda s: abs(s.t0 - t))]
        fr = None
        for s in active:
            a = min(ease((t - (s.t0 - XF / 2)) / XF) if s is not scenes[0] else 1.0,
                    ease(((s.t1 + XF / 2) - t) / XF) if s is not scenes[-1] else 1.0)
            img = s.render(t)
            if fr is None:
                fr = img
            else:
                fr = Image.blend(fr, img, a)
        # fade de entrada/saida geral
        g_in = ease(t / 0.8)
        g_out = ease((dur - t) / 1.6)
        # textos
        paste_alpha(fr, handle, W / 2, 225, ease(t / 0.6))
        paste_alpha(fr, label, W / 2, 300, ease((t - 0.2) / 0.8) * (0.0 if hook is None else 1.0) + (1.0 if hook is None else 0.0) * ease(t / 0.8))
        paste_alpha(fr, rule, W / 2, 350, ease((t - 0.4) / 0.8))
        if hook:
            hk = spec["hook"]
            paste_alpha(fr, hook, W / 2, TEXT_Y, fade_alpha(t, hk["t0"], hk["t1"], 0.7, 0.6))
        in_verse = verse and verse[0] <= t <= verse[1]
        in_hi = any(h0 <= t <= h1 for h0, h1, _ in his)
        if not in_verse and not in_hi:
            for c0, c1, lay in caps:
                a = fade_alpha(t, c0, c1, 0.25, 0.25)
                if a > 0:
                    paste_alpha(fr, lay, W / 2, TEXT_Y, a)
        if verse:
            v0, v1, vt, vr = verse
            a = fade_alpha(t, v0, v1, 0.6, 0.6)
            paste_alpha(fr, vt, W / 2, TEXT_Y - 10, a)
            paste_alpha(fr, vr, W / 2, TEXT_Y - 10 + vt.height / 2 + 34, a)
        for h0, h1, lay in his:
            paste_alpha(fr, lay, W / 2, TEXT_Y, fade_alpha(t, h0, h1, 0.5, 0.5))
        if endref:
            paste_alpha(fr, endref, W / 2, 1770, fade_alpha(t, dur - 6.5, dur, 0.8, 1.4))
        if cta:
            paste_alpha(fr, cta[2], W / 2, TEXT_Y, fade_alpha(t, cta[0], cta[1], 0.6, 0.8))
        arr = np.asarray(fr.convert("RGB")).astype(np.float32)
        arr += grain[..., None] * 255 * 0.5
        k = min(g_in, g_out)
        arr = arr * k + np.array(CREAM, np.float32) * (1 - k)
        return np.clip(arr, 0, 255).astype(np.uint8)

    if preview:
        ims = [Image.fromarray(render(t)).resize((360, 640)) for t in preview]
        sheet = Image.new("RGB", (360 * len(ims), 640), "white")
        for i, im in enumerate(ims):
            sheet.paste(im, (i * 360, 0))
        sheet.save(out)
        print("preview", out)
        return

    music = os.path.join(base, "_music.wav")
    build_music(dur, music)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", spec["audio"], "-i", music,
           "-filter_complex", "[1:a]adelay=%d|%d,volume=1.0[v];[2:a]volume=0.16[m];[v][m]amix=inputs=2:duration=longest:normalize=0,afade=t=out:st=%.2f:d=1.5,loudnorm=I=-14:TP=-1.5:LRA=11[a]" % (int(spec.get("voice_delay", 0.6) * 1000), int(spec.get("voice_delay", 0.6) * 1000), dur - 1.5),
           "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-t", str(dur), "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(dur * FPS)
    for i in range(n):
        p.stdin.write(render(i / FPS).tobytes())
        if i % 300 == 0:
            print(f"{i}/{n}", flush=True)
    p.stdin.close()
    p.wait()
    print("ok", out)


if __name__ == "__main__":
    main()
