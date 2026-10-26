#!/usr/bin/env python3
"""
Simulador do ESP32 (para testar sem o circuito físico).

Expõe as MESMAS rotas HTTP do firmware:
    /                -> página HTML
    /temperaturec    -> temperatura em °C   (texto, 2 casas decimais)
    /temperaturef    -> temperatura em °F
    /status          -> "LIGADO" ou "DESLIGADO" (cooler)

A temperatura é simulada em malha fechada, igual ao hardware real:
o ambiente esquenta e esfria ao longo do tempo (ciclo de 90 s) e o cooler,
quando ligado, retira calor. O controle usa a mesma histerese do firmware
(liga acima de 28 °C, desliga abaixo de 25 °C).

Uso isolado:   python mock_esp32.py            (porta 8080)
"""
import argparse
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEMP_LIGAR = 28.0
TEMP_DESLIGAR = 25.0
PERIODO_S = 90.0          # duração de um ciclo "frio -> quente -> frio"


class Estado:
    def __init__(self):
        self.temp = 24.0
        self.cooler = False
        self.t0 = time.time()
        self.lock = threading.Lock()

    def passo(self):
        """Avança a simulação em 1 segundo."""
        with self.lock:
            t = time.time() - self.t0
            ambiente = 24.0 + 9.0 * math.sin(2 * math.pi * t / PERIODO_S)  # 15..33 °C
            self.temp += (ambiente - self.temp) * 0.08          # troca de calor com o ambiente
            if self.cooler:
                self.temp -= 0.5                                 # efeito do cooler
            self.temp += random.uniform(-0.05, 0.05)             # ruído do sensor
            # Controle ON/OFF com histerese (igual ao firmware)
            if not self.cooler and self.temp > TEMP_LIGAR:
                self.cooler = True
            elif self.cooler and self.temp < TEMP_DESLIGAR:
                self.cooler = False

    def ler(self):
        with self.lock:
            return self.temp, self.cooler


def _criar_handler(estado):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            temp, cooler = estado.ler()
            if self.path == "/temperaturec":
                corpo, tipo = f"{temp:.2f}", "text/plain"
            elif self.path == "/temperaturef":
                corpo, tipo = f"{temp * 1.8 + 32:.2f}", "text/plain"
            elif self.path == "/status":
                corpo, tipo = ("LIGADO" if cooler else "DESLIGADO"), "text/plain"
            elif self.path == "/":
                corpo = (f"<html><body><h2>ESP32 simulado</h2>"
                         f"<p>{temp:.2f} &deg;C</p><p>Cooler: "
                         f"{'LIGADO' if cooler else 'DESLIGADO'}</p></body></html>")
                tipo = "text/html"
            else:
                self.send_error(404)
                return
            dados = corpo.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{tipo}; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *args):   # silencia o log de cada requisição
            pass

    return Handler


def iniciar(porta=8080):
    """Sobe o simulador em threads de fundo e devolve o servidor."""
    estado = Estado()

    def ciclo():
        while True:
            time.sleep(1.0)
            estado.passo()

    threading.Thread(target=ciclo, daemon=True).start()
    servidor = ThreadingHTTPServer(("0.0.0.0", porta), _criar_handler(estado))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Simulador do ESP32")
    ap.add_argument("--porta", type=int, default=8080)
    args = ap.parse_args()
    iniciar(args.porta)
    print(f"ESP32 simulado em http://127.0.0.1:{args.porta}  (Ctrl+C para sair)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
