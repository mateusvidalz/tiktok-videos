#!/usr/bin/env python3
"""
ARENA NEON v2 - motor de videos gamificados de fisica (TikTok 9:16, 68s)

Uso:
  python3 arena_engine.py --mode rei --shapes times --seed 7 --out video.mp4
  python3 arena_engine.py --mode plinko --shapes misto --seed 3 --out video.mp4 --title "QUAL FORMA|PONTUA MAIS?"
  python3 arena_engine.py --mode fuga --preview 1,22,40,56 --out prev/teste   (so PNGs, rapido)

Modos (todos com fisica real via pymunk: gravidade, atrito, quique, rotacao, inercia):
  fuga    anel girando com uma abertura; cada peca que escapa = +1 e gera 2 novas no centro
  duelo   4 pecas grandes com 100 de vida batendo num anel que encolhe; ultima de pe vence
  prensa  prensa desce e cada peca esmagada vira 2 menores; quando lota, a arena explode
  plinko  pecas caem por pinos e paletas girando ate baldes com pontos (10 a 500)
  rei     pecas caem numa plataforma que balanca cada vez mais; quem cai sai; mais pecas em cima vence

Formas (--shapes):
  times   cada time tem uma forma propria (CIRCULO, TRIANGULO, QUADRADO, HEXAGONO) e o nome do time e a forma
  misto   cada peca nasce com forma aleatoria (circulo, triangulo, quadrado, pauzinho, hexagono); times por cor
  circulo / triangulo / quadrado / pauzinho / hexagono   todas as pecas daquela forma; times por cor

Gamificacao (regra fixa): UMA unica pausa de 5s aos 24s (camera lenta + pergunta + contagem 5-4-3-2-1).
Gancho nos primeiros 1,6s e revelacao do vencedor nos ultimos 4s.
Dependencias: pip install pymunk pycairo numpy ; ffmpeg ; fonte Inter Display.
"""
import argparse, math, random, subprocess, wave, os, sys, json
import numpy as np
import cairo
import pymunk

W, H, FPS = 1080, 1920, 30
DUR = 68.0
SR = 44100
FONT = "Inter Display"
STOP_T, STOP_LEN = 24.0, 5.0
END_T = DUR - 4.0
K = END_T / 54.0   # escala dos tempos de jogo (calibrados para 54s)

BG_CENTER = (0.085, 0.063, 0.20)
BG_EDGE = (0.024, 0.020, 0.06)
INK = (1, 1, 1)
YEL = (1, 0.93, 0.35)
DANGER = (1.0, 0.30, 0.37)
ARENA = (0.92, 0.9, 1.0)
COLORS = [("CIANO", (0.13, 0.89, 1.00)), ("ROSA", (1.00, 0.24, 0.80)),
          ("LIMA", (0.66, 1.00, 0.24)), ("OURO", (1.00, 0.76, 0.24))]
SHAPE_NAMES = {"circulo": "CÍRCULO", "triangulo": "TRIÂNGULO", "quadrado": "QUADRADO",
               "hexagono": "HEXÁGONO", "pauzinho": "PAUZINHO"}
ALL_SHAPES = ["circulo", "triangulo", "quadrado", "pauzinho", "hexagono"]
CX, CY = W / 2, 1040

MODE_TEXT = {
    "fuga":   {"title": ["QUEM ESCAPA", "MAIS DO ANEL?"], "hook": "1 SAÍDA. 4 TIMES.", "q": "QUEM VAI ESCAPAR MAIS?"},
    "duelo":  {"title": ["QUEM FICA", "DE PÉ?"], "hook": "SÓ 1 SOBREVIVE", "q": "QUEM VOCÊ ACHA QUE VENCE?"},
    "prensa": {"title": ["QUEM DOMINA", "A ARENA?"], "hook": "A PRENSA MULTIPLICA", "q": "QUEM VAI DOMINAR?"},
    "plinko": {"title": ["QUEM FAZ", "MAIS PONTOS?"], "hook": "CAIU, PONTUOU", "q": "QUEM VAI FAZER MAIS PONTOS?"},
    "rei":    {"title": ["QUEM FICA", "NA PLATAFORMA?"], "hook": "CAIU, PERDEU", "q": "QUEM VAI FICAR EM CIMA?"},
}


def ease_out_back(x):
    c1, c3 = 1.70158, 2.70158
    x = max(0.0, min(1.0, x))
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def lighten(rgb, k):
    return tuple(c + (1 - c) * k for c in rgb)


# ================= AUDIO =================
class Audio:
    def __init__(self):
        self.buf = np.zeros(int(SR * (DUR + 1.5)), dtype=np.float32)
        self.last = {}
        self.slot = {}

    def add(self, t, sig, gain=1.0):
        i = int(t * SR)
        if i >= len(self.buf) or i < 0:
            return
        n = min(len(sig), len(self.buf) - i)
        self.buf[i:i + n] += sig[:n] * gain

    def gate(self, t, key, min_gap, voices=1):
        k = (key, int(t / min_gap))
        c = self.slot.get(k, 0)
        if c >= voices:
            return False
        self.slot[k] = c + 1
        return True

    def knock(self, t, size, shape, gain):
        """batida de colisao: timbre depende da forma, tom depende do tamanho"""
        if not self.gate(t, "knock", 0.03, 3):
            return
        base = 2600 / max(8, size)
        n = int(0.12 * SR)
        tt = np.arange(n) / SR
        if shape == "circulo":      # vidro/borracha: senoide com pitch drop
            f = base * 9 * np.exp(-tt * 25) + base * 3
            s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 45)
        elif shape == "pauzinho":   # madeira: dois parciais curtos
            s = (np.sin(2 * np.pi * base * 7 * tt) + 0.6 * np.sin(2 * np.pi * base * 17.3 * tt)) * np.exp(-tt * 70)
        elif shape == "triangulo":  # metalico: parciais inarmonicos
            s = (np.sin(2 * np.pi * base * 11 * tt) + 0.5 * np.sin(2 * np.pi * base * 27.1 * tt)
                 + 0.3 * np.sin(2 * np.pi * base * 41.7 * tt)) * np.exp(-tt * 30)
        else:                        # quadrado/hexagono: bloco plastico
            s = (np.sin(2 * np.pi * base * 5 * tt) + 0.4 * np.sin(2 * np.pi * base * 13 * tt)) * np.exp(-tt * 55)
        s = s + np.random.uniform(-1, 1, n) * np.exp(-tt * 400) * 0.5
        self.add(t, s.astype(np.float32), min(0.55, gain))

    def pop(self, t, pitch=1.0, gain=0.5):
        if not self.gate(t, "pop", 0.035, 2):
            return
        n = int(0.09 * SR)
        tt = np.arange(n) / SR
        f = (950 * pitch) * np.exp(-tt * 18) + 220 * pitch
        s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 38) + np.random.uniform(-1, 1, n) * np.exp(-tt * 300) * 0.4
        self.add(t, s.astype(np.float32), gain)

    def thump(self, t, gain=0.6, key="thump", min_gap=0.05):
        if not self.gate(t, key, min_gap):
            return
        n = int(0.25 * SR)
        tt = np.arange(n) / SR
        f = 140 * np.exp(-tt * 14) + 45
        self.add(t, (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 12)).astype(np.float32), gain)

    def boom(self, t, gain=0.9):
        n = int(1.2 * SR)
        tt = np.arange(n) / SR
        noise = np.convolve(np.random.uniform(-1, 1, n), np.ones(40) / 40, mode="same")
        sub = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-tt * 3) + 30) / SR)
        self.add(t, ((noise * 2.2 + sub) * np.exp(-tt * 3.2)).astype(np.float32), gain)

    def tone(self, t, freq, dur, gain=0.25, wave_="tri"):
        n = int(dur * SR)
        tt = np.arange(n) / SR
        s = 2 / np.pi * np.arcsin(np.sin(2 * np.pi * freq * tt)) if wave_ == "tri" else np.sin(2 * np.pi * freq * tt)
        env = np.minimum(1, tt * 60) * np.exp(-tt * 3.5)
        self.add(t, (s * env).astype(np.float32), gain)

    def ding(self, t, value):
        f = {10: 523.25, 50: 659.25, 100: 783.99, 500: 1046.5}.get(value, 880)
        if self.gate(t, "ding", 0.05, 2):
            self.tone(t, f, 0.5, 0.16 if value < 500 else 0.28, "sine")
            if value >= 500:
                self.tone(t + 0.06, f * 1.5, 0.6, 0.2, "sine")

    def whoosh(self, t, down=True, dur=0.6, gain=0.35):
        n = int(dur * SR)
        tt = np.arange(n) / SR
        noise = np.random.uniform(-1, 1, n)
        cut = np.linspace(60, 6, n) if down else np.linspace(6, 60, n)
        out = np.zeros(n)
        acc = 0.0
        for i in range(n):  # filtro passa-baixa variavel simples
            a = 1.0 / cut[i]
            acc += (noise[i] - acc) * a
            out[i] = acc
        env = np.sin(np.pi * tt / dur)
        self.add(t, (out * env * 6).astype(np.float32), gain)

    def tick(self, t, hi=False):
        self.tone(t, 1760 if hi else 1320, 0.08, 0.22, "sine")

    def fanfare(self, t):
        for dt, f in [(0, 523.25), (0.12, 659.25), (0.24, 783.99), (0.42, 1046.5), (0.42, 783.99), (0.42, 659.25)]:
            self.tone(t + dt, f, 1.2, 0.2)

    def bed(self):
        bpm = 118
        beat = 60 / bpm
        t, i = 0.0, 0
        while t < DUR + 0.5:
            in_stop = STOP_T <= t < STOP_T + STOP_LEN
            if not in_stop:
                n = int(0.25 * SR)
                tt = np.arange(n) / SR
                f = 140 * np.exp(-tt * 14) + 45
                self.add(t, (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 12)).astype(np.float32), 0.26)
                n = int(0.04 * SR)
                hat = np.random.uniform(-1, 1, n) * np.exp(-np.arange(n) / SR * 120)
                self.add(t + beat / 2, hat.astype(np.float32), 0.06)
                if i % 2 == 1:  # palma
                    n = int(0.12 * SR)
                    cl = np.convolve(np.random.uniform(-1, 1, n), [1, -1], "same") * np.exp(-np.arange(n) / SR * 35)
                    self.add(t, cl.astype(np.float32), 0.07)
            root = [55, 55, 65.41, 49][(i // 4) % 4]
            self.tone(t, root, beat * 0.9, 0.11 if not in_stop else 0.05, "sine")
            if t > END_T - 8 and t < END_T:  # tensao final: oitava acima
                self.tone(t + beat / 2, root * 4, beat * 0.4, 0.05)
            t += beat
            i += 1

    def write(self, path):
        b = np.tanh(self.buf * 1.3)
        peak = np.max(np.abs(b)) or 1
        b = (b / peak * 0.9 * 32767).astype(np.int16)
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes(b.tobytes())


# ================= PARTICULAS =================
class Particles:
    def __init__(self):
        self.p = []

    def burst(self, x, y, rgb, n=14, speed=500, size=7, life=0.7):
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            s = random.uniform(0.3, 1) * speed
            self.p.append([x, y, math.cos(a) * s, math.sin(a) * s, life, life, rgb, size * random.uniform(0.5, 1.2)])

    def step(self, dt):
        for q in self.p:
            q[0] += q[2] * dt; q[1] += q[3] * dt; q[3] += 900 * dt; q[2] *= 0.985; q[4] -= dt
        self.p = [q for q in self.p if q[4] > 0]

    def draw(self, ctx):
        for x, y, vx, vy, life, ml, rgb, s in self.p:
            a = max(0, life / ml)
            ctx.set_source_rgba(*rgb, a)
            ctx.arc(x, y, s * (0.4 + 0.6 * a), 0, 2 * math.pi)
            ctx.fill()


# ================= DESENHO =================
def make_background():
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    c = cairo.Context(surf)
    g = cairo.RadialGradient(CX, CY, 50, CX, CY, 1300)
    g.add_color_stop_rgb(0, *BG_CENTER)
    g.add_color_stop_rgb(1, *BG_EDGE)
    c.set_source(g)
    c.paint()
    c.set_source_rgba(1, 1, 1, 0.05)
    for y in range(0, H, 54):
        for x in range(0, W, 54):
            c.arc(x + 27, y + 27, 1.6, 0, 2 * math.pi)
            c.fill()
    return surf


def draw_ball(ctx, x, y, r, rgb, alpha=1.0, glow=True):
    if glow:
        g = cairo.RadialGradient(x, y, r * 0.6, x, y, r * 2.1)
        g.add_color_stop_rgba(0, *rgb, 0.45 * alpha)
        g.add_color_stop_rgba(1, *rgb, 0)
        ctx.set_source(g)
        ctx.arc(x, y, r * 2.1, 0, 2 * math.pi)
        ctx.fill()
    g = cairo.RadialGradient(x - r * 0.35, y - r * 0.4, r * 0.1, x, y, r)
    g.add_color_stop_rgba(0, *lighten(rgb, 0.75), alpha)
    g.add_color_stop_rgba(0.55, *rgb, alpha)
    g.add_color_stop_rgba(1, *(c * 0.55 for c in rgb), alpha)
    ctx.set_source(g)
    ctx.arc(x, y, r, 0, 2 * math.pi)
    ctx.fill()
    ctx.set_source_rgba(1, 1, 1, 0.75 * alpha)
    ctx.arc(x - r * 0.38, y - r * 0.42, r * 0.18, 0, 2 * math.pi)
    ctx.fill()


def shape_local_verts(kind, r):
    if kind == "triangulo":
        return [(r * math.cos(a), r * math.sin(a)) for a in (-math.pi / 2, math.pi / 6, 5 * math.pi / 6)]
    if kind == "quadrado":
        s = r * 0.8
        return [(-s, -s), (s, -s), (s, s), (-s, s)]
    if kind == "hexagono":
        return [(r * math.cos(a), r * math.sin(a)) for a in [i * math.pi / 3 for i in range(6)]]
    if kind == "pauzinho":
        return [(-r * 1.3, -r * 0.28), (r * 1.3, -r * 0.28), (r * 1.3, r * 0.28), (-r * 1.3, r * 0.28)]
    return None


def draw_poly(ctx, pts, rgb, alpha=1.0, glow=True):
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    rr = max(math.hypot(p[0] - cx, p[1] - cy) for p in pts)

    def path():
        ctx.move_to(*pts[0])
        for p in pts[1:]:
            ctx.line_to(*p)
        ctx.close_path()
    if glow:
        g = cairo.RadialGradient(cx, cy, rr * 0.5, cx, cy, rr * 2.0)
        g.add_color_stop_rgba(0, *rgb, 0.4 * alpha)
        g.add_color_stop_rgba(1, *rgb, 0)
        ctx.set_source(g)
        ctx.arc(cx, cy, rr * 2.0, 0, 2 * math.pi)
        ctx.fill()
    path()
    g = cairo.LinearGradient(cx - rr, cy - rr, cx + rr, cy + rr)
    g.add_color_stop_rgba(0, *lighten(rgb, 0.6), alpha)
    g.add_color_stop_rgba(0.5, *rgb, alpha)
    g.add_color_stop_rgba(1, *(c * 0.55 for c in rgb), alpha)
    ctx.set_source(g)
    ctx.fill_preserve()
    ctx.set_source_rgba(*lighten(rgb, 0.8), 0.9 * alpha)
    ctx.set_line_width(2.5)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.stroke()


def draw_icon(ctx, kind, x, y, r, rgb, alpha=1.0, glow=False, angle=0.0):
    if kind == "circulo":
        draw_ball(ctx, x, y, r, rgb, alpha, glow)
        return
    ca, sa = math.cos(angle), math.sin(angle)
    pts = [(x + vx * ca - vy * sa, y + vx * sa + vy * ca) for vx, vy in shape_local_verts(kind, r)]
    draw_poly(ctx, pts, rgb, alpha, glow)


def neon_stroke(ctx, rgb, width, alpha=1.0):
    for wmul, a in ((4.0, 0.10), (2.2, 0.22), (1.0, 1.0)):
        ctx.set_source_rgba(*rgb, a * alpha)
        ctx.set_line_width(width * wmul)
        ctx.stroke_preserve()
    ctx.new_path()


def text(ctx, s, x, y, size, rgb=INK, stroke=(0, 0, 0), sw=10, align="center", weight="Black", alpha=1.0, scale=1.0, maxw=980):
    ctx.save()
    ctx.translate(x, y)
    ctx.scale(scale, scale)
    ctx.select_font_face(FONT + " " + weight, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
    xb, yb, tw, th, xa, ya = ctx.text_extents(s)
    if tw > maxw:
        sw = sw * maxw / tw
        size = size * maxw / tw
        ctx.set_font_size(size)
        xb, yb, tw, th, xa, ya = ctx.text_extents(s)
    ox = {"center": -tw / 2 - xb, "left": -xb, "right": -tw - xb}[align]
    ctx.move_to(ox, th / 2)
    ctx.text_path(s)
    if sw:
        ctx.set_source_rgba(*stroke, alpha)
        ctx.set_line_width(sw)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.stroke_preserve()
    ctx.set_source_rgba(*rgb, alpha)
    ctx.fill()
    ctx.restore()


def rrect(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


# ================= TIMES =================
class Teams:
    def __init__(self, shapes):
        self.mode = shapes
        self.list = []
        if shapes == "times":
            kinds = random.sample(["circulo", "triangulo", "quadrado", "hexagono", "pauzinho"], 4)
            for (cn, rgb), k in zip(COLORS, kinds):
                self.list.append({"name": SHAPE_NAMES[k], "rgb": rgb, "kind": k})
        else:
            for cn, rgb in COLORS:
                self.list.append({"name": cn, "rgb": rgb, "kind": shapes if shapes in ALL_SHAPES else "misto"})

    def kind_for(self, team):
        k = self.list[team]["kind"]
        return random.choice(ALL_SHAPES) if k == "misto" else k

    def icon(self, team):
        k = self.list[team]["kind"]
        return "circulo" if k == "misto" else k


# ================= HUD =================
def draw_header(ctx, lines):
    text(ctx, lines[0], CX, 250, 66, sw=12)
    text(ctx, lines[1], CX, 330, 78, rgb=YEL, sw=12)


def draw_scoreboard(ctx, teams, scores, y=440, alive=None):
    cw = 225
    x0 = CX - cw * 2
    lead = max(scores)
    for i, sc in enumerate(scores):
        tm = teams.list[i]
        x = x0 + i * cw + 8
        dead = alive is not None and not alive[i]
        rrect(ctx, x, y - 42, cw - 16, 84, 42)
        ctx.set_source_rgba(0, 0, 0, 0.45)
        ctx.fill_preserve()
        neon_stroke(ctx, tm["rgb"], 3 if (sc == lead and lead > 0 and not dead) else 1.5, 0.35 if dead else 1)
        draw_icon(ctx, teams.icon(i), x + 42, y, 20, tm["rgb"], 0.35 if dead else 1)
        text(ctx, "X" if dead else str(sc), x + 128, y, 46, sw=6, alpha=0.4 if dead else 1, maxw=120)


def draw_progress(ctx, t, teams):
    x, y, w, h = 140, 1600, 800, 14
    rrect(ctx, x, y, w, h, 7)
    ctx.set_source_rgba(1, 1, 1, 0.12)
    ctx.fill()
    p = min(1, t / END_T)
    if p > 0.01:
        rrect(ctx, x, y, max(14, w * p), h, 7)
        g = cairo.LinearGradient(x, 0, x + w, 0)
        for i, tm in enumerate(teams.list):
            g.add_color_stop_rgb(i / 3, *tm["rgb"])
        ctx.set_source(g)
        ctx.fill()
    text(ctx, "0:%02d" % int(math.ceil(max(0, END_T - t))), CX, y + 60, 40, sw=6)


def draw_hook(ctx, s, t):
    if t > 1.9:
        return
    k = ease_out_back(t / 0.35)
    a = 1 if t < 1.5 else max(0, 1 - (t - 1.5) / 0.4)
    text(ctx, s, CX, 640, 92, rgb=YEL, sw=16, alpha=a, scale=k)


def draw_stop(ctx, t, qtext, teams):
    lt = t - STOP_T
    if lt < 0 or lt > STOP_LEN:
        return
    a = min(1, lt / 0.15) * (1 if lt < STOP_LEN - 0.3 else max(0, (STOP_LEN - lt) / 0.3))
    ctx.set_source_rgba(0, 0, 0, 0.35 * a)
    ctx.rectangle(0, 0, W, H)
    ctx.fill()
    k = ease_out_back(lt / 0.35)
    ctx.save()
    ctx.translate(CX, 760)
    ctx.scale(k, k)
    rrect(ctx, -470, -190, 940, 380, 48)
    ctx.set_source_rgba(0.1, 0.05, 0.25, 0.96 * a)
    ctx.fill_preserve()
    neon_stroke(ctx, YEL, 4, a)
    text(ctx, "PAUSA! ESCOLHE O SEU", 0, -120, 50, rgb=YEL, sw=8, alpha=a, maxw=860)
    words = qtext.split()
    mid = len(words) // 2 + (len(words) % 2)
    text(ctx, " ".join(words[:mid]), 0, -35, 64, sw=10, alpha=a, maxw=860)
    text(ctx, " ".join(words[mid:]), 0, 45, 64, sw=10, alpha=a, maxw=860)
    for i, tm in enumerate(teams.list):
        draw_icon(ctx, teams.icon(i), -165 + i * 110, 125, 26, tm["rgb"], a)
    ctx.restore()
    text(ctx, "COMENTA SEU PALPITE", CX, 1000, 50, sw=10, alpha=a)
    n = 5 - int(lt)
    if 1 <= n <= 5:
        kk = ease_out_back((lt % 1.0) / 0.3)
        text(ctx, str(n), CX, 1150, 130, rgb=YEL, sw=16, alpha=a, scale=kk)


def draw_winner(ctx, t, teams, winner, reason):
    lt = t - END_T
    if lt < 0 or winner is None:
        return
    tm = teams.list[winner]
    a = min(1, lt / 0.25)
    ctx.set_source_rgba(0, 0, 0, 0.55 * a)
    ctx.rectangle(0, 0, W, H)
    ctx.fill()
    k = ease_out_back(lt / 0.5)
    draw_icon(ctx, teams.icon(winner), CX, CY - 260, 110 * k, tm["rgb"], a, glow=True, angle=lt * 0.8)
    text(ctx, reason, CX, CY - 60, 56, sw=10, alpha=a)
    text(ctx, tm["name"] + " VENCEU!", CX, CY + 50, 104, rgb=tm["rgb"], sw=16, alpha=a, scale=k)
    if lt > 0.9:
        b = min(1, (lt - 0.9) / 0.3)
        text(ctx, "ACERTOU? COMENTA AQUI", CX, CY + 200, 54, sw=10, alpha=b)
        text(ctx, "SEGUE PRA PRÓXIMA BATALHA", CX, CY + 280, 44, rgb=YEL, sw=8, alpha=b)


# ================= BASE DOS MODOS =================
class Base:
    def __init__(self, audio, parts, teams):
        self.space = pymunk.Space()
        self.space.iterations = 15
        self.audio, self.parts, self.teams = audio, parts, teams
        self.pieces = []
        self.winner, self.reason, self.t = None, "", 0.0
        self.space.on_collision(None, None, post_solve=self._sound)

    def _sound(self, arb, space, data):
        if not arb.is_first_contact:
            return
        imp = arb.total_impulse.length
        a, b = arb.shapes
        pa = getattr(a, "piece", None) or getattr(b, "piece", None)
        if pa is None:
            return
        m = pa["body"].mass
        v = imp / max(0.1, m)
        if v < 120:
            return
        self.audio.knock(self.t, pa["r"], pa["kind"], 0.08 + min(0.45, v / 2500))

    def add_piece(self, x, y, team, r, vx=0, vy=0, elastic=0.9, friction=0.4, kind=None, angle=None, density=1.0):
        kind = kind or self.teams.kind_for(team)
        verts = shape_local_verts(kind, r)
        if verts is None:
            mass = density * r * r / 400
            body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, r))
            sh = pymunk.Circle(body, r)
        else:
            mass = density * r * r / 400 * (0.6 if kind == "pauzinho" else 1)
            body = pymunk.Body(mass, pymunk.moment_for_poly(mass, verts))
            sh = pymunk.Poly(body, verts, radius=1.5)
        body.position = (x, y)
        body.velocity = (vx, vy)
        body.angle = random.uniform(0, 6.28) if angle is None else angle
        body.angular_velocity = random.uniform(-3, 3)
        sh.elasticity = elastic
        sh.friction = friction
        sh.collision_type = 1
        self.space.add(body, sh)
        p = {"body": body, "shape": sh, "team": team, "r": r, "kind": kind}
        sh.piece = p
        self.pieces.append(p)
        return p

    def remove_piece(self, p):
        if p in self.pieces:
            self.space.remove(p["body"], p["shape"])
            self.pieces.remove(p)

    def draw_piece(self, ctx, p, rgb=None, alpha=1.0):
        rgb = rgb or self.teams.list[p["team"]]["rgb"]
        b = p["body"]
        if p["kind"] == "circulo":
            draw_ball(ctx, b.position.x, b.position.y, p["r"], rgb, alpha)
        else:
            pts = [b.local_to_world(v) for v in p["shape"].get_vertices()]
            draw_poly(ctx, [(q.x, q.y) for q in pts], rgb, alpha)

    def counts(self):
        c = [0, 0, 0, 0]
        for p in self.pieces:
            c[p["team"]] += 1
        return c


# ---------------- FUGA ----------------
class Fuga(Base):
    R, GAP = 420, 0.62

    def __init__(self, *a):
        super().__init__(*a)
        self.space.gravity = (0, 650)
        self.ring = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        self.ring.position = (CX, CY)
        self.ring.angular_velocity = 0.85 * random.choice([-1, 1])
        self.space.add(self.ring)
        n = 120
        for i in range(n):
            a0 = self.GAP / 2 + (2 * math.pi - self.GAP) * i / n
            a1 = self.GAP / 2 + (2 * math.pi - self.GAP) * (i + 1) / n
            s = pymunk.Segment(self.ring, (self.R * math.cos(a0), self.R * math.sin(a0)),
                               (self.R * math.cos(a1), self.R * math.sin(a1)), 7)
            s.elasticity, s.friction = 1.0, 0.0
            self.space.add(s)
        self.scores = [0, 0, 0, 0]
        self.ghosts, self.floats = [], []
        for i in list(range(4)) * 2:
            a = random.uniform(0, 2 * math.pi)
            self.add_piece(CX + math.cos(a) * 60, CY + math.sin(a) * 60, i, 24, math.cos(a) * 600, math.sin(a) * 600 - 200, elastic=0.97, friction=0.05)

    def update(self, t, dt):
        self.t = t
        for _ in range(4):
            self.space.step(dt / 4)
        for p in list(self.pieces):
            b = p["body"]
            v = b.velocity
            if v.length < 280:
                b.velocity = v * 1.04 + (random.uniform(-40, 40), -60)
            if v.length > 1500:
                b.velocity = v * (1500 / v.length)
            d = math.hypot(b.position.x - CX, b.position.y - CY)
            if d > self.R + 34:
                if t < END_T:
                    tm = p["team"]
                    self.scores[tm] += 1
                    self.ghosts.append([p, 1.2])
                    self.floats.append([b.position.x, b.position.y, 1.0, tm])
                    self.parts.burst(b.position.x, b.position.y, self.teams.list[tm]["rgb"], n=10, speed=380, size=6)
                    self.audio.pop(t, 0.8 + tm * 0.18, 0.4)
                    self.space.remove(b, p["shape"])
                    self.pieces.remove(p)
                    inside = sum(1 for q in self.pieces if q["team"] == tm)
                    spawn = 2 if (len(self.pieces) < 110 and inside < 30) else 1
                    for _ in range(spawn):
                        a = random.uniform(math.pi * 1.1, math.pi * 1.9)
                        s = random.uniform(450, 750)
                        self.add_piece(CX + random.uniform(-30, 30), CY + random.uniform(-30, 30), tm, 24,
                                       math.cos(a) * s, math.sin(a) * s, elastic=0.97, friction=0.05)
                else:
                    self.remove_piece(p)
        for g in self.ghosts:   # pecas que escaparam seguem caindo com gravidade (fora da simulacao)
            b = g[0]["body"]
            b.velocity = (b.velocity.x, b.velocity.y + 900 * dt)
            b.position = (b.position.x + b.velocity.x * dt, b.position.y + b.velocity.y * dt)
            b.angle += b.angular_velocity * dt
            g[1] -= dt
        self.ghosts = [g for g in self.ghosts if g[1] > 0]
        for f in self.floats:
            f[1] -= 120 * dt; f[2] -= dt
        self.floats = [f for f in self.floats if f[2] > 0]
        if t >= END_T and self.winner is None:
            m = max(self.scores)
            self.winner = self.scores.index(m)
            self.reason = "%d FUGAS" % m

    def draw(self, ctx):
        ang = self.ring.angle
        ctx.arc(CX, CY, self.R, ang + self.GAP / 2, ang + 2 * math.pi - self.GAP / 2)
        neon_stroke(ctx, ARENA, 9)
        for p, life in self.ghosts:
            self.draw_piece(ctx, p, alpha=max(0, life / 1.2) * 0.8)
        for p in self.pieces:
            self.draw_piece(ctx, p)
        for x, y, life, tm in self.floats:
            text(ctx, "+1", x, y, 44, rgb=self.teams.list[tm]["rgb"], sw=6, alpha=max(0, life))

    def board(self, ctx):
        draw_scoreboard(ctx, self.teams, self.scores)


# ---------------- DUELO ----------------
class Duelo(Base):
    R0, R1 = 470, 300

    def __init__(self, *a):
        super().__init__(*a)
        self.space.gravity = (0, 0)
        self.hp = [100.0] * 4
        self.alive = [True] * 4
        self.speed = 620
        self.flash = [0.0] * 4
        self.floats = []
        self.cool = {}
        self.stages = [33.0 * K, 43.0 * K, END_T - 0.5]
        self.bleed = None
        for i in range(4):
            a = math.pi / 4 + i * math.pi / 2
            v = pymunk.Vec2d(1, 0).rotated(random.uniform(0, 6.28)) * self.speed
            self.add_piece(CX + math.cos(a) * 220, CY + math.sin(a) * 220, i, 62, v.x, v.y, elastic=1.0, friction=0.2)
        self.space.on_collision(1, 1, begin=self.hit, post_solve=self._sound)

    def floor(self, team):
        return 0.0 if (self.bleed and self.bleed[0] == team) else 10.0

    def radius(self, t):
        k = min(1, t / (48.0 * K))
        return self.R0 + (self.R1 - self.R0) * (k * k * (3 - 2 * k))

    def mult(self, t):
        return 0.16 + t / (53.0 * K) * 0.30

    def hit(self, arb, space, data):
        a, b = arb.shapes[0].piece, arb.shapes[1].piece
        t = self.t
        if t > END_T:
            return True
        key = tuple(sorted((a["team"], b["team"])))
        if t - self.cool.get(key, -9) < 0.4:
            return True
        self.cool[key] = t
        va, vb = a["body"].velocity.length, b["body"].velocity.length
        for att, vic, vatt in ((a, b, va), (b, a, vb)):
            crit = random.random() < 0.15
            # pontas machucam mais: triangulo e pauzinho ganham bonus
            sharp = {"triangulo": 1.25, "pauzinho": 1.15}.get(att["kind"], 1.0)
            dmg = random.uniform(2, 5) * self.mult(t) * (2 if crit else 1) * sharp * (0.7 + 0.6 * vatt / max(1, va + vb))
            tv = vic["team"]
            self.hp[tv] = max(min(self.hp[tv], self.floor(tv)), self.hp[tv] - dmg)
            self.flash[tv] = 0.25
            vp = vic["body"].position
            self.floats.append([vp.x, vp.y - 80, 0.8, "-%d" % max(1, round(dmg)) + ("!" if crit else ""), crit])
        p = (a["body"].position + b["body"].position) / 2
        self.parts.burst(p.x, p.y, (1, 1, 1), n=8, speed=420, size=5, life=0.4)
        self.audio.thump(t, 0.7)
        return True

    def update(self, t, dt):
        self.t = t
        for _ in range(4):
            self.space.step(dt / 4)
            R = self.radius(t)
            for p in self.pieces:
                b = p["body"]
                d = pymunk.Vec2d(b.position.x - CX, b.position.y - CY)
                rr = p["r"] * (1.25 if p["kind"] == "pauzinho" else 1.0)
                if d.length > R - rr:
                    n = d.normalized()
                    v = b.velocity
                    if v.dot(n) > 0:
                        b.velocity = v - 2 * v.dot(n) * n
                        b.angular_velocity += random.uniform(-4, 4)  # parede gira a peca
                        self.audio.knock(t, p["r"], p["kind"], 0.3)
                        if 40 * K < t < END_T:
                            tm = p["team"]
                            self.hp[tm] = max(min(self.hp[tm], self.floor(tm)), self.hp[tm] - 1.5)
                            self.flash[tm] = 0.2
                    b.position = (CX + n.x * (R - rr), CY + n.y * (R - rr))
        sp = self.speed * (1 + 0.4 * min(1, t / (50 * K)))
        for p in self.pieces:
            v = p["body"].velocity
            if v.length > 1:
                p["body"].velocity = v * (sp / v.length)
            p["body"].angular_velocity = max(-6, min(6, p["body"].angular_velocity))
        living = [i for i in range(4) if self.alive[i]]
        si = 4 - len(living)
        if si < 3 and self.bleed is None and t >= self.stages[si] - 3.0:
            weak = min(living, key=lambda i: (self.hp[i], random.random()))
            self.bleed = (weak, max(self.hp[weak], 1), t, self.stages[si])
        if self.bleed:
            tm_, h0, t0, t1 = self.bleed
            self.hp[tm_] = min(self.hp[tm_], h0 * max(0, (t1 - t) / (t1 - t0)))
            self.flash[tm_] = 0.1 if int(t * 8) % 2 == 0 else 0
            if t >= t1:
                self.hp[tm_] = 0
        for p in list(self.pieces):
            tm = p["team"]
            if self.hp[tm] <= 0 and self.alive[tm]:
                self.alive[tm] = False
                b = p["body"]
                self.parts.burst(b.position.x, b.position.y, self.teams.list[tm]["rgb"], n=40, speed=700, size=10, life=1.0)
                self.audio.boom(t, 0.7)
                self.remove_piece(p)
                self.bleed = None
        for i in range(4):
            self.flash[i] = max(0, self.flash[i] - dt)
        for f in self.floats:
            f[1] -= 90 * dt; f[2] -= dt
        self.floats = [f for f in self.floats if f[2] > 0]
        alive = [i for i in range(4) if self.alive[i]]
        if self.winner is None and (len(alive) == 1 or t >= END_T):
            self.winner = max(alive, key=lambda i: self.hp[i])
            self.reason = "ÚLTIMO DE PÉ" if len(alive) == 1 else "MAIS VIDA NO FIM"

    def draw(self, ctx):
        R = self.radius(self.t)
        danger = self.t > 40 * K
        ctx.arc(CX, CY, R, 0, 2 * math.pi)
        neon_stroke(ctx, DANGER if danger else ARENA, 9)
        for p in self.pieces:
            tm = p["team"]
            rgb = self.teams.list[tm]["rgb"]
            self.draw_piece(ctx, p, rgb=lighten(rgb, 0.6) if self.flash[tm] > 0 else rgb)
            b = p["body"]
            text(ctx, "%d" % math.ceil(self.hp[tm]), b.position.x, b.position.y, 40, sw=7)
        for x, y, life, s, crit in self.floats:
            text(ctx, s, x, y, 52 if crit else 38, rgb=YEL if crit else INK, sw=6, alpha=max(0, life / 0.8))
        if self.bleed and self.t < END_T:
            bp = [q for q in self.pieces if q["team"] == self.bleed[0]]
            if bp:
                text(ctx, "SANGRANDO!", bp[0]["body"].position.x, bp[0]["body"].position.y - 100, 36, rgb=DANGER, sw=6)
        if danger and self.t < END_T and int(self.t * 3) % 2 == 0:
            text(ctx, "ZONA DE PERIGO", CX, CY + R + 60, 40, rgb=DANGER, sw=6)

    def board(self, ctx):
        draw_scoreboard(ctx, self.teams, [int(math.ceil(h)) for h in self.hp], alive=self.alive)


# ---------------- PRENSA ----------------
class Prensa(Base):
    X0, X1, Y0, Y1 = 150, 930, 640, 1500
    TARGET = 220

    def __init__(self, *a):
        super().__init__(*a)
        self.space.gravity = (0, 1400)
        st = self.space.static_body
        for a_, b_ in [((self.X0, self.Y0 - 400), (self.X0, self.Y1)), ((self.X0, self.Y1), (self.X1, self.Y1)),
                       ((self.X1, self.Y1), (self.X1, self.Y0 - 400))]:
            s = pymunk.Segment(st, a_, b_, 6)
            s.elasticity, s.friction = 0.5, 0.6
            self.space.add(s)
        self.pw, self.px, self.py = 260, CX, self.Y0 - 60
        self.phase, self.period = 0.0, 2.2
        self.exploded, self.shake = False, 0.0
        for tm in range(4):
            for k in range(3):
                self.add_piece(random.uniform(self.X0 + 50, self.X1 - 50), random.uniform(self.Y0, self.Y1 - 200), tm, 36, elastic=0.35, friction=0.6)

    def update(self, t, dt):
        self.t = t
        if not self.exploded:
            prev = self.phase
            self.phase = (self.phase + dt / self.period) % 1.0
            if self.phase < prev:
                self.px = random.uniform(self.X0 + self.pw / 2 + 10, self.X1 - self.pw / 2 - 10)
            ph = self.phase
            top, bottom = self.Y0 - 60, self.Y1 - 24
            if ph < 0.45:
                self.py = top
            elif ph < 0.60:
                k = (ph - 0.45) / 0.15
                self.py = top + (bottom - top) * k * k
            elif ph < 0.68:
                self.py = bottom
            else:
                self.py = bottom - (bottom - top) * (ph - 0.68) / 0.32
            if 0.45 <= ph < 0.62:
                hit = [p for p in self.pieces if abs(p["body"].position.x - self.px) < self.pw / 2 + p["r"] * 0.3
                       and p["body"].position.y - p["r"] * 0.2 < self.py]
                goal = 12 * (self.TARGET / 12.0) ** (min(t, 52.0 * K) / (52.0 * K))
                budget = max(0, int(goal - len(self.pieces)) + 1)
                random.shuffle(hit)
                crushed = hit[:budget]
                for p in hit[budget:]:   # empurrados para o lado pela prensa
                    b = p["body"]
                    side = -1 if b.position.x < self.px else 1
                    nx = min(self.X1 - p["r"] - 8, max(self.X0 + p["r"] + 8, self.px + side * (self.pw / 2 + p["r"] + 2)))
                    b.position = (nx, b.position.y)
                    b.velocity = (side * 300, -200)
                    b.angular_velocity = side * 6
                if crushed:
                    self.shake = 0.15
                    self.audio.thump(t, 0.85, key="press")
                for p in crushed:
                    b = p["body"]
                    x, y, tm, r, kind = b.position.x, b.position.y, p["team"], p["r"], p["kind"]
                    self.remove_piece(p)
                    self.parts.burst(x, y, self.teams.list[tm]["rgb"], n=8, speed=400, size=6, life=0.5)
                    self.audio.pop(t, 0.8 + tm * 0.2, 0.4)
                    nr = max(14, r * 0.9)
                    for side in (-1, 1):
                        nx = min(self.X1 - nr - 8, max(self.X0 + nr + 8, self.px + side * (self.pw / 2 + nr + random.uniform(2, 40))))
                        self.add_piece(nx, min(self.Y1 - nr - 8, y + random.uniform(-20, 20)), tm, nr,
                                       side * random.uniform(200, 500), random.uniform(-900, -500), elastic=0.35, friction=0.6,
                                       kind=kind if self.teams.mode != "misto" else None)
            if len(self.pieces) >= self.TARGET:
                self.explode(t, "ARENA LOTOU")
        for _ in range(3):
            self.space.step(dt / 3)
        for p in list(self.pieces):
            b = p["body"]
            if b.position.y > H + 200 or b.position.x < -300 or b.position.x > W + 300:
                self.remove_piece(p)
        self.shake = max(0, self.shake - dt)
        if t >= END_T and self.winner is None:
            c = self.final_counts if self.exploded else self.counts()
            self.winner = c.index(max(c))
            self.reason = "%d PEÇAS" % max(c)
            if not self.exploded:
                self.explode(t, "TEMPO ESGOTADO")

    def explode(self, t, why):
        self.final_counts = self.counts()
        self.exploded, self.why = True, why
        self.audio.boom(t, 1.0)
        for p in self.pieces:
            b = p["body"]
            d = pymunk.Vec2d(b.position.x - CX, b.position.y - CY)
            n = d.normalized() if d.length > 1 else pymunk.Vec2d(0, -1)
            b.velocity = n * random.uniform(1200, 2200) + (0, -600)
            b.angular_velocity = random.uniform(-15, 15)
            p["shape"].filter = pymunk.ShapeFilter(categories=0)
        self.parts.burst(CX, CY, (1, 1, 1), n=60, speed=1200, size=10, life=1.2)
        self.shake = 0.6

    def draw(self, ctx):
        if self.shake > 0:
            ctx.translate(random.uniform(-12, 12) * self.shake * 4, random.uniform(-12, 12) * self.shake * 4)
        if not self.exploded:
            ctx.move_to(self.X0, self.Y0 - 120); ctx.line_to(self.X0, self.Y1)
            ctx.line_to(self.X1, self.Y1); ctx.line_to(self.X1, self.Y0 - 120)
            neon_stroke(ctx, ARENA, 8)
            col = DANGER if len(self.pieces) / self.TARGET > 0.8 else ARENA
            text(ctx, "%d / %d" % (len(self.pieces), self.TARGET), CX, self.Y1 + 55, 42, rgb=col, sw=6)
        for p in self.pieces:
            self.draw_piece(ctx, p)
        if not self.exploded:
            x = self.px - self.pw / 2
            ctx.rectangle(self.px - 24, 520, 48, self.py - 520 - 90)
            ctx.set_source_rgba(0.5, 0.5, 0.65, 0.9)
            ctx.fill()
            rrect(ctx, x, self.py - 90, self.pw, 90, 14)
            g = cairo.LinearGradient(0, self.py - 90, 0, self.py)
            g.add_color_stop_rgb(0, 0.75, 0.75, 0.9)
            g.add_color_stop_rgb(1, 0.35, 0.33, 0.5)
            ctx.set_source(g)
            ctx.fill_preserve()
            neon_stroke(ctx, DANGER, 3)
        elif self.t < END_T and not (STOP_T <= self.t < STOP_T + STOP_LEN):
            text(ctx, self.why, CX, CY, 90, rgb=DANGER, sw=14)

    def board(self, ctx):
        draw_scoreboard(ctx, self.teams, self.final_counts if self.exploded else self.counts())


# ---------------- PLINKO ----------------
class Plinko(Base):
    VALUES = [10, 50, 100, 500, 100, 50, 10]
    BX0, BX1, BTOP, BBOT = 110, 970, 1360, 1540

    def __init__(self, *a):
        super().__init__(*a)
        self.space.gravity = (0, 1300)
        st = self.space.static_body
        self.pegs = []
        rows = 9
        for r_ in range(rows):
            y = 620 + r_ * 80
            off = 0 if r_ % 2 == 0 else 54
            x = 150 + off
            while x < 940:
                c = pymunk.Circle(st, 9, (x, y))
                c.elasticity, c.friction = 0.6, 0.3
                self.space.add(c)
                self.pegs.append((x, y))
                x += 108
        for x_ in (self.BX0, self.BX1):
            s = pymunk.Segment(st, (x_, 520), (x_, self.BBOT), 6)
            s.elasticity = 0.6
            self.space.add(s)
        s = pymunk.Segment(st, (self.BX0, self.BBOT), (self.BX1, self.BBOT), 6)
        s.elasticity, s.friction = 0.2, 0.8
        self.space.add(s)
        self.bw = (self.BX1 - self.BX0) / len(self.VALUES)
        for i in range(1, len(self.VALUES)):
            x = self.BX0 + i * self.bw
            s = pymunk.Segment(st, (x, self.BTOP), (x, self.BBOT), 5)
            s.elasticity = 0.4
            self.space.add(s)
        # duas paletas girando (kinematic) para caos fisico
        self.spinners = []
        for (x, y, w_) in [(330, 1010, 1.6), (750, 1010, -1.6)]:
            b = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
            b.position = (x, y)
            b.angular_velocity = w_
            s = pymunk.Segment(b, (-75, 0), (75, 0), 9)
            s.elasticity, s.friction = 0.8, 0.4
            self.space.add(b, s)
            self.spinners.append(b)
        self.scores = [0, 0, 0, 0]
        self.next_drop, self.turn = 0.3, random.randrange(4)
        self.landed = {}
        self.floats = []
        self.flash_bucket = {}

    def update(self, t, dt):
        self.t = t
        if t >= self.next_drop and t < END_T - 3.5:
            self.add_piece(random.uniform(260, 820), 540, self.turn, 22, random.uniform(-80, 80), 0, elastic=0.5, friction=0.4)
            self.turn = (self.turn + 1) % 4
            self.next_drop = t + 0.22
        for _ in range(4):
            self.space.step(dt / 4)
        for p in list(self.pieces):
            b = p["body"]
            if b.position.y > self.BTOP + 30 and id(p) not in self.landed:
                i = int((b.position.x - self.BX0) // self.bw)
                i = max(0, min(len(self.VALUES) - 1, i))
                v = self.VALUES[i]
                self.landed[id(p)] = t
                if t < END_T:
                    self.scores[p["team"]] += v
                    self.floats.append([b.position.x, self.BTOP - 20, 1.0, "+%d" % v, p["team"], v >= 500])
                    self.audio.ding(t, v)
                    self.flash_bucket[i] = 0.3
                    if v >= 500:
                        self.parts.burst(b.position.x, self.BTOP, self.teams.list[p["team"]]["rgb"], n=24, speed=600, size=7)
            if id(p) in self.landed and t - self.landed[id(p)] > 1.4:
                self.parts.burst(b.position.x, b.position.y, self.teams.list[p["team"]]["rgb"], n=5, speed=200, size=4, life=0.4)
                self.remove_piece(p)
            elif b.position.y > H:
                self.remove_piece(p)
        for f in self.floats:
            f[1] -= 100 * dt; f[2] -= dt
        self.floats = [f for f in self.floats if f[2] > 0]
        for k in list(self.flash_bucket):
            self.flash_bucket[k] -= dt
        if t >= END_T and self.winner is None:
            m = max(self.scores)
            self.winner = self.scores.index(m)
            self.reason = "%d PONTOS" % m

    def draw(self, ctx):
        for (x, y) in self.pegs:
            ctx.arc(x, y, 9, 0, 2 * math.pi)
            ctx.set_source_rgba(*ARENA, 0.9)
            ctx.fill()
        for b in self.spinners:
            a = b.angle
            ctx.move_to(b.position.x - 75 * math.cos(a), b.position.y - 75 * math.sin(a))
            ctx.line_to(b.position.x + 75 * math.cos(a), b.position.y + 75 * math.sin(a))
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            neon_stroke(ctx, YEL, 12)
        ctx.move_to(self.BX0, 520); ctx.line_to(self.BX0, self.BBOT); ctx.line_to(self.BX1, self.BBOT); ctx.line_to(self.BX1, 520)
        neon_stroke(ctx, ARENA, 7)
        for i, v in enumerate(self.VALUES):
            x = self.BX0 + i * self.bw
            if i > 0:
                ctx.move_to(x, self.BTOP); ctx.line_to(x, self.BBOT)
                neon_stroke(ctx, ARENA, 5)
            hot = self.flash_bucket.get(i, 0) > 0
            col = YEL if v >= 500 else (DANGER if v == 10 else INK)
            if hot:
                ctx.rectangle(x + 4, self.BTOP, self.bw - 8, self.BBOT - self.BTOP)
                ctx.set_source_rgba(*col, 0.18)
                ctx.fill()
            text(ctx, str(v), x + self.bw / 2, self.BBOT + 36, 34, rgb=col, sw=6)
        for p in self.pieces:
            self.draw_piece(ctx, p)
        for x, y, life, s, tm, big in self.floats:
            text(ctx, s, x, y, 52 if big else 34, rgb=self.teams.list[tm]["rgb"], sw=6, alpha=max(0, life))

    def board(self, ctx):
        draw_scoreboard(ctx, self.teams, self.scores)


# ---------------- REI DA PLATAFORMA ----------------
class Rei(Base):
    PW = 680

    def __init__(self, *a):
        super().__init__(*a)
        self.space.gravity = (0, 1100)
        self.plat = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        self.plat.position = (CX, 1180)
        s = pymunk.Segment(self.plat, (-self.PW / 2, 0), (self.PW / 2, 0), 14)
        s.elasticity, s.friction = 0.2, 0.9
        self.space.add(self.plat, s)
        # pivo triangular decorativo
        self.next_drop, self.turn = 0.2, random.randrange(4)
        self.fallen = [0, 0, 0, 0]
        self.dropped = 0
        self.phase0 = random.uniform(0, 6.28)
        self.out = []
        self.st = 0.0

    def target_angle(self, t, st):
        amp = 0.04 + 0.42 * min(1, t / (50.0 * K)) ** 1.6
        return amp * math.sin(self.phase0 + st * (1.1 + 0.6 * min(1, t / (50 * K))))

    def update(self, t, dt):
        self.t = t
        if t >= self.next_drop and t < 40 * K:
            self.add_piece(random.uniform(CX - 260, CX + 260), 560, self.turn, 30, random.uniform(-60, 60), 0, elastic=0.25, friction=0.8)
            self.turn = (self.turn + 1) % 4
            self.dropped += 1
            self.next_drop = t + 0.42
        n = 4
        for _ in range(n):
            h = dt / n
            self.st += h
            desired = self.target_angle(t, self.st)
            self.plat.angular_velocity = (desired - self.plat.angle) / h
            self.space.step(h)
        for p in list(self.pieces):
            b = p["body"]
            if b.position.y > 1480:
                if t < END_T:
                    self.fallen[p["team"]] += 1
                    self.parts.burst(b.position.x, 1500, self.teams.list[p["team"]]["rgb"], n=10, speed=380, size=6)
                    self.audio.pop(t, 0.6 + p["team"] * 0.15, 0.35)
                self.out.append([p, 1.0])
                self.space.remove(b, p["shape"])
                self.pieces.remove(p)
        for g in self.out:
            b = g[0]["body"]
            b.velocity = (b.velocity.x, b.velocity.y + 1100 * dt)
            b.position = (b.position.x + b.velocity.x * dt, b.position.y + b.velocity.y * dt)
            g[1] -= dt
        self.out = [g for g in self.out if g[1] > 0]
        if t >= END_T and self.winner is None:
            c = self.counts()
            m = max(c)
            ties = [i for i in range(4) if c[i] == m]
            self.winner = min(ties, key=lambda i: self.fallen[i])
            self.reason = "%d PEÇAS EM CIMA" % m

    def draw(self, ctx):
        a = self.plat.angle
        x, y = self.plat.position
        hx, hy = self.PW / 2 * math.cos(a), self.PW / 2 * math.sin(a)
        ctx.move_to(x, y + 20); ctx.line_to(x - 60, y + 160); ctx.line_to(x + 60, y + 160); ctx.close_path()
        ctx.set_source_rgba(0.35, 0.33, 0.5, 1)
        ctx.fill_preserve()
        neon_stroke(ctx, ARENA, 3)
        ctx.move_to(x - hx, y - hy); ctx.line_to(x + hx, y + hy)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        neon_stroke(ctx, DANGER if self.t > 40 * K else ARENA, 26)
        ctx.move_to(60, 1480); ctx.line_to(W - 60, 1480)
        ctx.set_dash([18, 14])
        neon_stroke(ctx, DANGER, 3, 0.6)
        ctx.set_dash([])
        text(ctx, "CAIU AQUI, SAIU", CX, 1520, 32, rgb=DANGER, sw=5)
        for p, life in self.out:
            self.draw_piece(ctx, p, alpha=max(0, life) * 0.7)
        for p in self.pieces:
            self.draw_piece(ctx, p)
        if 40 * K <= self.t < END_T and int(self.t * 3) % 2 == 0:
            text(ctx, "SEM REFORÇOS!", CX, 620, 46, rgb=DANGER, sw=7)

    def board(self, ctx):
        draw_scoreboard(ctx, self.teams, self.counts())


MODES = {"fuga": Fuga, "duelo": Duelo, "prensa": Prensa, "plinko": Plinko, "rei": Rei}


def render(mode, seed, out, shapes="times", title=None, question=None, preview=None):
    random.seed(seed)
    np.random.seed(seed)
    txt = dict(MODE_TEXT[mode])
    if title:
        parts_ = title.split("|")
        txt["title"] = [parts_[0].strip(), parts_[1].strip() if len(parts_) > 1 else ""]
    if question:
        txt["q"] = question
    teams = Teams(shapes)
    audio = Audio()
    audio.bed()
    parts = Particles()
    sim = MODES[mode](audio, parts, teams)
    bg = make_background()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surf)
    proc = None
    if not preview:
        proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
                                 "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                                 "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", out + ".v.mp4"],
                                stdin=subprocess.PIPE)
    # sons da pausa unica
    audio.whoosh(STOP_T - 0.15, down=True)
    for k in range(5):
        audio.tick(STOP_T + 0.2 + k, hi=(k == 4))
    audio.whoosh(STOP_T + STOP_LEN - 0.4, down=False)
    audio.whoosh(END_T - 0.6, down=False, dur=0.7, gain=0.4)
    win_sound = False
    for f in range(int(DUR * FPS)):
        t = f / FPS
        slow = 1.0
        if STOP_T <= t < STOP_T + STOP_LEN:
            slow = 0.15
        if t >= END_T:
            slow = 0.35
        dt = slow / FPS
        sim.update(t, dt)
        parts.step(dt)
        if sim.winner is not None and not win_sound and t >= END_T:
            audio.fanfare(t)
            win_sound = True
            parts.burst(CX, CY - 260, teams.list[sim.winner]["rgb"], n=80, speed=1100, size=11, life=1.6)
        ctx.save()
        ctx.set_source_surface(bg, 0, 0)
        ctx.paint()
        ctx.save()
        sim.draw(ctx)
        ctx.restore()
        parts.draw(ctx)
        draw_header(ctx, txt["title"])
        sim.board(ctx)
        draw_progress(ctx, t, teams)
        draw_hook(ctx, txt["hook"], t)
        draw_stop(ctx, t, txt["q"], teams)
        draw_winner(ctx, t, teams, sim.winner, sim.reason)
        ctx.restore()
        if preview and f in preview:
            surf.write_to_png("%s_%05.1f.png" % (out, t))
        if proc:
            proc.stdin.write(surf.get_data())
    if proc:
        proc.stdin.close()
        proc.wait()
        wav = out + ".wav"
        audio.write(wav)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", out + ".v.mp4", "-i", wav, "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], check=True)
        os.remove(out + ".v.mp4")
        os.remove(wav)
    return {"winner": teams.list[sim.winner]["name"] if sim.winner is not None else None, "reason": sim.reason,
            "teams": [t_["name"] for t_ in teams.list]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=list(MODES), required=True)
    ap.add_argument("--shapes", default="times", choices=["times", "misto"] + ALL_SHAPES)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", help="Duas linhas separadas por |")
    ap.add_argument("--question", help="Pergunta da pausa de 5s")
    ap.add_argument("--preview", help="segundos para salvar PNG em vez de video, ex: 1,21,40,56")
    a = ap.parse_args()
    prev = set(int(float(s) * FPS) for s in a.preview.split(",")) if a.preview else None
    print(json.dumps(render(a.mode, a.seed, a.out, a.shapes, a.title, a.question, prev), ensure_ascii=False))
