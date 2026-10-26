#!/usr/bin/env python3
"""
Computação Gráfica - Monitoramento de temperatura via ESP32

Lê continuamente a temperatura do servidor web do ESP32 (requisições HTTP)
e manipula o valor em uma cena animada:

  * ROSTO (emoji 3D brilhante, com degradê, brilho e sombra) cuja expressão
    e cor mudam conforme a temperatura;
  * TERMÔMETRO ao lado, cujo nível sobe/desce suavemente com a medida;
  * VENTILADOR girando no canto quando o cooler está ligado.

O firmware liga o cooler acima de 28 °C e só desliga abaixo de 25 °C
(histerese). As faixas do rosto seguem esses valores:
    < 20 °C        FRIO      azul      - tremendo, dentes batendo, neve
    20 a 25 °C     IDEAL     amarelo   - feliz, piscando, com brilhinhos
    25 a 28 °C     ATENÇÃO   laranja   - preocupado, olhando de lado, suando
    >= 28 °C       QUENTE    vermelho  - língua de fora, suando, vapor
    sem conexão    rosto cinza dormindo (zzz)
    falha sensor   rosto cinza com olhos em X

Uso:
    python cg_temperatura.py --ip 192.168.0.50     # ESP32 real (IP do Serial Monitor)
    python cg_temperatura.py --sim                 # sem hardware (usa mock_esp32.py)

Dependências:  pip install matplotlib numpy requests
"""
import argparse
import math
import threading
import time

import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.animation import FuncAnimation
from matplotlib.colors import to_rgb
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch, Polygon, Rectangle, Wedge

# --------------------------------------------------------------------------
# Configuração das faixas (limite superior, nome, cor)
# --------------------------------------------------------------------------
FAIXAS = [
    (20.0, "FRIO", "#55AEF5"),
    (25.0, "IDEAL", "#FFC93C"),
    (28.0, "ATENÇÃO", "#FF9F43"),
    (math.inf, "QUENTE", "#EE4B3F"),
]
COR_OFFLINE = "#8C99A6"
TEMP_LIGAR, TEMP_DESLIGAR = 28.0, 25.0      # mesmos valores do firmware


def faixa_de(temp):
    for limite, nome, cor in FAIXAS:
        if temp < limite:
            return nome, np.array(to_rgb(cor))


# --------------------------------------------------------------------------
# Fonte de dados: consulta o ESP32 em uma thread (não trava a animação)
# --------------------------------------------------------------------------
class FonteESP32:
    def __init__(self, base_url, intervalo=2.0):
        self.base = base_url.rstrip("/")
        self.intervalo = intervalo
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._sessao = requests.Session()
        self.temp = float("nan")
        self.cooler = None
        self.online = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _get(self, rota):
        r = self._sessao.get(self.base + rota, timeout=1.5)
        r.raise_for_status()
        return r.text.strip()

    @staticmethod
    def _converter(texto):
        # O firmware responde "--" quando o sensor falha
        return float("nan") if texto in ("--", "") else float(texto)

    def _loop(self):
        while not self._parar.is_set():
            inicio = time.time()
            try:
                temp = self._converter(self._get("/temperaturec"))
                cooler = self._get("/status") == "LIGADO"
                with self._lock:
                    self.temp, self.cooler, self.online = temp, cooler, True
            except (requests.RequestException, ValueError):
                with self._lock:
                    self.online = False
            self._parar.wait(max(0.0, self.intervalo - (time.time() - inicio)))

    def snapshot(self):
        with self._lock:
            return {"temp": self.temp, "cooler": self.cooler, "online": self.online}

    def parar(self):
        self._parar.set()


# --------------------------------------------------------------------------
# Visualização: rosto (emoji 3D) + termômetro
# --------------------------------------------------------------------------
T_MIN, T_MAX = 10.0, 40.0                     # escala do termômetro


class Visualizador:
    FUNDO = "#14181f"
    # geometria da cena (coordenadas de dados do eixo, x: 0..10, y: -2.6..10)
    ROSTO = (3.3, 5.8, 2.5)                   # centro x, centro y, raio
    TERM_X, TERM_BULBO_Y = 8.2, 1.8
    TERM_Y0, TERM_Y1 = 2.4, 8.8               # posições de T_MIN e T_MAX
    N_CAMADAS = 44                            # camadas do degradê radial do rosto

    def __init__(self, fonte):
        self.fonte = fonte
        self.fase = 0
        self.cor = np.array(to_rgb(COR_OFFLINE))
        self.nivel = None                      # nível exibido no termômetro (suavizado)
        self.nivel_alvo = None
        self.din = []                          # artistas redesenhados a cada quadro
        self.neve = np.random.default_rng(3).uniform([0.4, 2.8], [6.2, 9.4], (8, 2))

        self.fig = plt.figure(figsize=(6.2, 7.4), facecolor=self.FUNDO)
        try:
            self.fig.canvas.manager.set_window_title("ESP32 - Computação Gráfica")
        except Exception:
            pass
        self._montar_cena()
        self.fig.suptitle("Monitoramento de temperatura - ESP32 + DS18B20",
                          color="white", fontsize=13, y=0.975)
        self.anim = FuncAnimation(self.fig, self.atualizar, interval=40,
                                  cache_frame_data=False)

    # ---- utilidades de desenho -------------------------------------------
    def _add(self, patch, z=4):
        patch.set_zorder(z)
        self.axc.add_patch(patch)
        self.din.append(patch)

    def _linha(self, xs, ys, lw=6, cor=None, z=5, **kw):
        ln, = self.axc.plot(xs, ys, color=cor or self.tinta, lw=lw, solid_capstyle="round",
                            solid_joinstyle="round", zorder=z, **kw)
        self.din.append(ln)

    def _texto(self, x, y, txt, **kw):
        self.din.append(self.axc.text(x, y, txt, **kw))

    def _limpar(self):
        for a in self.din:
            a.remove()
        self.din.clear()

    def _y_term(self, temp):
        t = np.clip(temp, T_MIN, T_MAX)
        return self.TERM_Y0 + (t - T_MIN) / (T_MAX - T_MIN) * (self.TERM_Y1 - self.TERM_Y0)

    # ---- cena: partes fixas e persistentes ---------------------------------
    def _montar_cena(self):
        ax = self.axc = self.fig.add_axes([0.0, 0.0, 1.0, 0.93])
        ax.set_facecolor(self.FUNDO)
        ax.set_xlim(0, 10); ax.set_ylim(-2.6, 10)
        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")

        # termômetro de vidro: tubo, bulbo, escala e marcas de histerese
        tx, by = self.TERM_X, self.TERM_BULBO_Y
        ax.add_patch(FancyBboxPatch((tx - 0.36, by), 0.72, 7.4, boxstyle="round,pad=0,rounding_size=0.36",
                                    fc="#e8edf2", ec="white", lw=2, zorder=1))
        ax.add_patch(Circle((tx, by), 0.72, fc="#e8edf2", ec="white", lw=2, zorder=1))
        for t in range(int(T_MIN), int(T_MAX) + 1, 5):
            y = self._y_term(t)
            ax.plot([tx + 0.42, tx + 0.62], [y, y], color="#c0c6d0", lw=1.5, zorder=2)
            ax.text(tx + 0.72, y, f"{t}", color="#c0c6d0", fontsize=9, va="center")
        ax.text(tx, self.TERM_Y1 + 0.95, "°C", color="#c0c6d0", fontsize=10, ha="center")
        for t, cor, rotulo in ((TEMP_LIGAR, "#EE4B3F", "liga 28"), (TEMP_DESLIGAR, "#55AEF5", "desliga 25")):
            y = self._y_term(t)
            ax.plot([tx - 0.62, tx - 0.42], [y, y], color=cor, lw=2.8, zorder=2)
            ax.text(tx - 0.72, y, rotulo, color="#c0c6d0", fontsize=8, ha="right", va="center")
        self.coluna = Rectangle((tx - 0.22, by), 0.44, 0.1, fc="gray", ec="none", zorder=3)
        ax.add_patch(self.coluna)
        self.bulbo = Circle((tx, by), 0.52, fc="gray", ec="none", zorder=4)
        ax.add_patch(self.bulbo)
        # reflexos de vidro
        ax.plot([tx - 0.28, tx - 0.28], [by + 0.7, self.TERM_Y1 + 0.2], color="white", alpha=0.6,
                lw=2.5, solid_capstyle="round", zorder=6)
        ax.add_patch(Circle((tx - 0.2, by + 0.2), 0.11, fc="white", ec="none", alpha=0.6, zorder=6))

        # rosto 3D: aura, sombra, camadas do degradê e reflexos (persistentes, só são movidos)
        self.aura = Circle((0, 0), 1, fc="none", ec="none", zorder=1)
        self.sombra = Ellipse((0, 0), 1, 1, fc="black", ec="none", alpha=0.35, zorder=2)
        self.camadas = [Circle((0, 0), 1, ec="none", zorder=3 + i * 0.001) for i in range(self.N_CAMADAS)]
        self.brilho = Ellipse((0, 0), 1, 1, angle=35, fc="white", ec="none", alpha=0.30, zorder=3.5)
        self.brilho2 = Circle((0, 0), 1, fc="white", ec="none", alpha=0.55, zorder=3.5)
        for p in [self.aura, self.sombra, *self.camadas, self.brilho, self.brilho2]:
            ax.add_patch(p)

        # textos
        self.txt_temp = ax.text(5.0, -0.5, "", color="white", fontsize=22, fontweight="bold",
                                ha="center", va="center")
        self.txt_faixa = ax.text(5.0, -1.25, "", color="white", fontsize=13, ha="center", va="center")
        self.txt_cooler = ax.text(5.0, -2.0, "", fontsize=13, fontweight="bold", ha="center", va="center")

    def _corpo(self, cx, cy, R, cy_base, estado):
        """Atualiza o degradê radial, os reflexos, a sombra e a aura do rosto."""
        base = self.cor
        escuro = base * 0.68
        claro = base + (1 - base) * 0.55
        n = self.N_CAMADAS
        for i, c in enumerate(self.camadas):
            t = i / (n - 1)
            c.set_center((cx - 0.26 * R * t, cy + 0.28 * R * t))
            c.set_radius(R * (1 - 0.86 * t))
            if t < 0.5:
                cor = escuro + (base - escuro) * (t / 0.5)
            else:
                cor = base + (claro - base) * ((t - 0.5) / 0.5)
            c.set_facecolor(tuple(np.clip(cor, 0, 1)))
        self.brilho.set_center((cx - 0.38 * R, cy + 0.58 * R))
        self.brilho.set_width(0.62 * R); self.brilho.set_height(0.26 * R)
        self.brilho2.set_center((cx - 0.60 * R, cy + 0.38 * R)); self.brilho2.set_radius(0.05 * R)
        self.sombra.set_center((cx, cy_base - R * 1.12))
        self.sombra.set_width(1.5 * R); self.sombra.set_height(0.2 * R)
        if estado in ("FRIO", "QUENTE"):                      # brilho ao redor do rosto
            a = 0.13 + 0.05 * math.sin(self.fase * 0.15)
            self.aura.set_center((cx, cy)); self.aura.set_radius(R * 1.2)
            self.aura.set_facecolor((*base, a))
            self.aura.set_visible(True)
        else:
            self.aura.set_visible(False)

    # ---- rosto: uma expressão por estado -----------------------------------
    def _rosto(self, estado, cooler):
        self._limpar()
        f = self.fase
        cx, cy, R = self.ROSTO
        cy_base = cy
        cy += 0.06 * math.sin(f * 0.12)                        # flutuando
        if estado == "FRIO":
            cx += 0.07 * math.sin(f * 2.3)                     # tremendo de frio
        self.tinta = tuple(np.clip(self.cor * 0.30, 0, 1))     # cor dos traços: tom escuro do rosto
        tinta = self.tinta
        self._corpo(cx, cy, R, cy_base, estado)

        def P(u, v):
            return cx + u * R, cy + v * R

        def curva(us, vs, **kw):
            us, vs = np.asarray(us, float), np.asarray(vs, float)
            self._linha(cx + us * R, cy + vs * R, **kw)

        def gota(u, v, alpha=1.0):
            x, y = P(u, v)
            self._add(Polygon([P(u - 0.07, v + 0.03), P(u + 0.07, v + 0.03), P(u, v + 0.24)],
                              fc="#9be3ff", ec="#2e86c1", lw=1.5, alpha=alpha), 6)
            self._add(Circle((x, y), 0.08 * R, fc="#9be3ff", ec="#2e86c1", lw=1.5, alpha=alpha), 6)
            self._add(Circle((x - 0.025 * R, y + 0.025 * R), 0.025 * R, fc="white", alpha=alpha), 7)

        def estrela(u, v, s, alpha=1.0):                       # brilhinho de 4 pontas
            k = 0.28
            pts = [(0, s), (k * s, k * s), (s, 0), (k * s, -k * s), (0, -s), (-k * s, -k * s),
                   (-s, 0), (-k * s, k * s)]
            self._add(Polygon([P(u + a, v + b) for a, b in pts], fc="#fff6c2", ec="none", alpha=alpha), 7)

        def olho_oval(u, v=0.2, piscando=False):
            if piscando:
                curva([u - 0.11, u + 0.11], [v, v], lw=5)
                return
            self._add(Ellipse(P(u, v), 0.21 * R, 0.34 * R, fc=tinta), 5)
            self._add(Circle(P(u - 0.035, v + 0.09), 0.045 * R, fc="white"), 6)
            self._add(Circle(P(u + 0.04, v - 0.07), 0.022 * R, fc="white", alpha=0.8), 6)

        def olho_branco(u, v=0.2, dx=0.0, dy=0.0):
            self._add(Circle(P(u, v), 0.19 * R, fc="white", ec=tinta, lw=2.5), 5)
            self._add(Circle(P(u + dx, v + dy), 0.085 * R, fc=tinta), 6)
            self._add(Circle(P(u + dx - 0.025, v + dy + 0.03), 0.025 * R, fc="white"), 7)

        def bochechas(cor):
            for u in (-0.64, 0.64):
                self._add(Ellipse(P(u, -0.12), 0.24 * R, 0.14 * R, fc=cor, alpha=0.40), 4)

        def sobrancelhas_preocupadas():
            curva([-0.62, -0.2], [0.54, 0.70], lw=6)
            curva([0.2, 0.62], [0.70, 0.54], lw=6)

        if estado == "IDEAL":                                  # feliz
            for u in (-0.38, 0.38):
                olho_oval(u, piscando=(f % 90) < 4)
            bochechas("#ff6f61")
            us = np.linspace(-0.5, 0.5, 30)
            base = -0.26 - 0.40 * (1 - (us / 0.5) ** 2)
            pts = np.vstack([np.c_[us, np.full_like(us, -0.26)], np.c_[us[::-1], base[::-1]]])
            boca = Polygon(np.c_[cx + pts[:, 0] * R, cy + pts[:, 1] * R], fc="#6b1d1d", ec=tinta, lw=3.5)
            self._add(boca, 5)
            dentes = Polygon([P(-0.5, -0.26), P(0.5, -0.26), P(0.5, -0.39), P(-0.5, -0.39)], fc="white")
            self._add(dentes, 6)
            lingua = Ellipse(P(0, -0.62), 0.5 * R, 0.28 * R, fc="#ff7a93")
            self._add(lingua, 6)
            dentes.set_clip_path(boca)
            lingua.set_clip_path(boca)
            estrela(0.98, 0.80, 0.12 + 0.05 * math.sin(f * 0.2))          # brilhinhos
            estrela(-1.02, 0.45, 0.08 + 0.04 * math.sin(f * 0.2 + 2))

        elif estado == "ATENÇÃO":                              # preocupado, olhando de lado
            olhar = 0.07 * math.sin(f * 0.09)
            for u in (-0.38, 0.38):
                olho_branco(u, dx=olhar, dy=-0.01)
            sobrancelhas_preocupadas()
            us = np.linspace(-0.38, 0.38, 30)
            curva(us, -0.52 + 0.05 * np.sin(us * 18), lw=6)    # boca ondulada, nervosa
            for u, off in ((0.80, 0), (-0.82, 35)):
                prog = ((f + off) % 70) / 70
                gota(u, 0.55 - 0.55 * prog, 1 - prog ** 2)

        elif estado == "QUENTE":                               # com muito calor
            curva([-0.58, -0.22, -0.58], [0.40, 0.26, 0.12], lw=7)    # olhos > <
            curva([0.58, 0.22, 0.58], [0.40, 0.26, 0.12], lw=7)
            bochechas("#ffb199")
            self._add(Ellipse(P(0, -0.45), 0.85 * R, 0.55 * R, fc="#5a1414", ec=tinta, lw=3.5), 5)
            lingua = 0.26 + 0.05 * math.sin(f * 0.5)           # língua "ofegante"
            self._add(Ellipse(P(0, -0.64), 0.40 * R, lingua * R, fc="#ff7a93", ec=tinta, lw=2), 6)
            curva([0, 0], [-0.60, -0.74], lw=2, z=7)
            for u0, off in ((-0.82, 0), (0.84, 30), (0.70, 55)):
                prog = ((f + off) % 60) / 60
                gota(u0, 0.6 - 0.7 * prog, 1 - prog ** 2)
            vs = np.linspace(1.08, 1.5, 20)                    # vapor saindo da cabeça
            for k, u0 in enumerate((-0.4, 0.0, 0.4)):
                curva(u0 + 0.07 * np.sin(vs * 9 - f * 0.25 + k * 2), vs, lw=4, cor="white", z=2, alpha=0.55)

        elif estado == "FRIO":                                 # tremendo, dentes batendo
            for u in (-0.38, 0.38):
                olho_branco(u, dx=0.0, dy=0.0)
            sobrancelhas_preocupadas()
            bochechas("#8e7cc3")
            x0, y0 = P(-0.42, -0.62)
            self._add(FancyBboxPatch((x0, y0), 0.84 * R, 0.26 * R, boxstyle="round,pad=0,rounding_size=0.25",
                                     fc="white", ec=tinta, lw=3), 5)
            curva([-0.42, 0.42], [-0.49, -0.49], lw=2.5, z=6)
            for u in np.linspace(-0.28, 0.28, 5):
                curva([u, u], [-0.62, -0.36], lw=2.5, z=6)
            if (f // 3) % 2 == 0:                              # marcas de tremor ao lado do rosto
                for lado in (-1, 1):
                    for dv in (0.0, 0.16, 0.32):
                        curva([lado * 1.12, lado * 1.24], [0.05 + dv, 0.15 + dv], lw=3, cor="white", z=2)
            y = 2.8 + (self.neve[:, 1] - 2.8 - f * 0.04) % 6.6          # neve caindo
            x = self.neve[:, 0] + 0.15 * np.sin(f * 0.05 + self.neve[:, 0])
            ln, = self.axc.plot(x, y, "*", color="white", ms=11, alpha=0.9, zorder=2)
            self.din.append(ln)

        elif estado == "OFFLINE":                              # dormindo
            for u in (-0.38, 0.38):
                us = np.linspace(u - 0.12, u + 0.12, 12)
                curva(us, 0.22 - 0.07 * (1 - ((us - u) / 0.12) ** 2), lw=5)
            self._add(Ellipse(P(0, -0.45), 0.16 * R, 0.10 * R, fc=tinta), 5)
            for k in range(3):
                prog = ((f / 1.5 + k * 25) % 75) / 75
                x, y = P(0.65 + 0.25 * prog, 0.7 + 0.6 * prog)
                self._texto(x, y, "z", color="white", fontsize=10 + 8 * prog, alpha=1 - prog,
                            fontweight="bold", zorder=6)

        else:                                                  # FALHA no sensor: olhos em X
            for u in (-0.38, 0.38):
                curva([u - 0.1, u + 0.1], [0.3, 0.1], lw=6)
                curva([u - 0.1, u + 0.1], [0.1, 0.3], lw=6)
            us = np.linspace(-0.4, 0.4, 30)
            curva(us, -0.5 + 0.06 * np.sin(us * 14 + f * 0.2), lw=6)

        if cooler:                                             # ventilador do cooler girando
            fx, fy, fr = 0.95, 8.7, 0.7
            self._add(Circle((fx, fy), fr, fc="none", ec="#5DADE2", lw=2.5), 3)
            for k in range(3):
                ang = f * 18 + k * 120
                self._add(Wedge((fx, fy), fr * 0.92, ang, ang + 55, fc="#5DADE2", ec="none"), 4)
            self._add(Circle((fx, fy), fr * 0.12, fc="white"), 5)
            self._texto(fx, fy - fr - 0.35, "cooler", color="#5DADE2", fontsize=9, ha="center", va="center")

    # ---- quadro da animação ----------------------------------------------
    def atualizar(self, _quadro=None):
        s = self.fonte.snapshot()
        temp = s["temp"]
        self.fase += 1

        if not s["online"]:
            estado, alvo, texto = "OFFLINE", np.array(to_rgb(COR_OFFLINE)), "-- °C"
            rotulo = "SEM CONEXÃO COM O ESP32"
        elif not math.isfinite(temp):
            estado, alvo, texto = "FALHA", np.array(to_rgb(COR_OFFLINE)), "-- °C"
            rotulo = "FALHA NO SENSOR"
        else:
            estado, alvo = faixa_de(temp)
            texto = f"{temp:.1f} °C  |  {temp * 1.8 + 32:.1f} °F"
            rotulo = f"Faixa: {estado}"
            if self.nivel is None:
                self.nivel = float(temp)                        # primeira leitura: sem animar
            self.nivel_alvo = float(temp)

        self.cor += (alvo - self.cor) * 0.12                    # transição suave de cor
        if self.nivel is not None:
            self.nivel += (self.nivel_alvo - self.nivel) * 0.10

        # termômetro: coluna sobe/desce e muda de cor junto com o rosto
        if self.nivel is not None:
            self.coluna.set_height(self._y_term(self.nivel) - self.TERM_BULBO_Y)
        self.coluna.set_facecolor(tuple(self.cor))
        self.bulbo.set_facecolor(tuple(self.cor))

        cooler = bool(s["cooler"]) and s["online"]
        self._rosto(estado, cooler)
        self.txt_temp.set_text(texto)
        self.txt_faixa.set_text(rotulo)
        if s["online"] and s["cooler"] is not None:
            self.txt_cooler.set_text("Cooler: LIGADO" if cooler else "Cooler: DESLIGADO")
            self.txt_cooler.set_color("#5DADE2" if cooler else "#95A5A6")
        else:
            self.txt_cooler.set_text("")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Rosto e termômetro animados pela temperatura do ESP32")
    ap.add_argument("--ip", help="IP do ESP32 (aparece no Serial Monitor)")
    ap.add_argument("--porta", type=int, default=80, help="porta HTTP (padrão 80)")
    ap.add_argument("--sim", action="store_true", help="usa o simulador mock_esp32.py")
    ap.add_argument("--intervalo", type=float, default=2.0, help="segundos entre leituras")
    args = ap.parse_args()

    if args.sim:
        import mock_esp32
        mock_esp32.iniciar(8080)
        base = "http://127.0.0.1:8080"
        print("Modo simulado: ESP32 virtual em", base)
    elif args.ip:
        base = f"http://{args.ip}" if args.porta == 80 else f"http://{args.ip}:{args.porta}"
    else:
        ap.error("informe --ip <IP do ESP32> ou use --sim")

    fonte = FonteESP32(base, intervalo=args.intervalo)
    Visualizador(fonte)
    try:
        plt.show()
    finally:
        fonte.parar()


if __name__ == "__main__":
    main()
