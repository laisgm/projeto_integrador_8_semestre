// Monitoramento de temperatura com ESP32 + DS18B20
// Controle ON/OFF do cooler (histerese), LCD I2C e servidor web.
//
// Bibliotecas: OneWire (Paul Stoffregen), DallasTemperature (Miles Burton),
//              LiquidCrystal_I2C, ESPAsyncWebServer e AsyncTCP (.zip)

#include <AsyncTCP.h>

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <LiquidCrystal_I2C.h>
#include <math.h>

// Configure sua rede Wi-Fi
const char* ssid = "NOME_DA_SUA_REDE";
const char* password = "SENHA_DA_SUA_REDE";

// Pinos e componentes
constexpr uint8_t ONE_WIRE_BUS = 23;
constexpr uint8_t PINO_RELE = 4;
constexpr uint8_t RELE_LIGADO = LOW;   // Ajustar caso o modulo seja ativo em HIGH
constexpr uint8_t RELE_DESLIGADO = HIGH;

// Limiares com histerese
constexpr float TEMP_LIGAR = 28.0f;
constexpr float TEMP_DESLIGAR = 25.0f;
constexpr unsigned long INTERVALO_LEITURA_MS = 2000;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
LiquidCrystal_I2C lcd(0x27, 16, 2);
AsyncWebServer server(80);

float temperaturaC = NAN;
bool coolerLigado = false;
unsigned long ultimaLeitura = 0;

// Uma unica leitura alimenta o LCD, o controle e as rotas HTTP.
void atualizarTemperatura() {
  sensors.requestTemperatures();
  float leitura = sensors.getTempCByIndex(0);

  if (leitura == DEVICE_DISCONNECTED_C || !isfinite(leitura) ||
      leitura < -55.0f || leitura > 125.0f) {
    Serial.println("Falha ao ler DS18B20; desligando cooler por seguranca.");
    temperaturaC = NAN;
    coolerLigado = false;
    digitalWrite(PINO_RELE, RELE_DESLIGADO);
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Falha no sensor");
    return;
  }

  temperaturaC = leitura;

  // Histerese: entre 25 e 28 graus, conserva o estado anterior.
  if (!coolerLigado && temperaturaC > TEMP_LIGAR) {
    coolerLigado = true;
  } else if (coolerLigado && temperaturaC < TEMP_DESLIGAR) {
    coolerLigado = false;
  }
  digitalWrite(PINO_RELE, coolerLigado ? RELE_LIGADO : RELE_DESLIGADO);

  Serial.printf("Temperatura: %.2f C | Cooler: %s\n",
                temperaturaC, coolerLigado ? "LIGADO" : "DESLIGADO");

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Temp: ");
  lcd.print(temperaturaC, 1);
  lcd.print(" C");
  lcd.setCursor(0, 1);
  lcd.print("Cooler: ");
  lcd.print(coolerLigado ? "ON" : "OFF");
}

const char paginaHTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Monitoramento ESP32</title>
  <style>
    body { font-family: Arial, sans-serif; text-align: center; padding: 30px; }
    .valor { font-size: 2em; margin: 12px; }
  </style>
</head>
<body>
  <h2>Monitoramento de Temperatura</h2>
  <p class="valor">Celsius: <span id="c">--</span> &deg;C</p>
  <p class="valor">Fahrenheit: <span id="f">--</span> &deg;F</p>
  <p class="valor">Cooler: <span id="status">--</span></p>
  <script>
    async function atualizar() {
      try {
        const [c, f, status] = await Promise.all([
          fetch('/temperaturec'), fetch('/temperaturef'), fetch('/status')
        ]);
        if (!c.ok || !f.ok || !status.ok) throw new Error('HTTP');
        document.getElementById('c').textContent = await c.text();
        document.getElementById('f').textContent = await f.text();
        document.getElementById('status').textContent = await status.text();
      } catch (erro) {
        document.getElementById('status').textContent = 'SEM CONEXAO';
      }
    }
    atualizar();
    setInterval(atualizar, 2000);
  </script>
</body>
</html>
)rawliteral";

void setup() {
  // Configura o estado de repouso antes de configurar como saida.
  digitalWrite(PINO_RELE, RELE_DESLIGADO);
  pinMode(PINO_RELE, OUTPUT);

  Serial.begin(115200);
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("Iniciando...");
  sensors.begin();
  atualizarTemperatura();
  ultimaLeitura = millis();

  WiFi.begin(ssid, password);
  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.print("\nEndereco IP: ");
  Serial.println(WiFi.localIP());

  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send_P(200, "text/html; charset=utf-8", paginaHTML);
  });
  server.on("/temperaturec", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/plain", isfinite(temperaturaC) ? String(temperaturaC, 2) : "--");
  });
  server.on("/temperaturef", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/plain", isfinite(temperaturaC) ? String(temperaturaC * 1.8f + 32.0f, 2) : "--");
  });
  server.on("/status", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/plain", coolerLigado ? "LIGADO" : "DESLIGADO");
  });
  server.begin();
}

void loop() {
  unsigned long agora = millis();
  if (agora - ultimaLeitura >= INTERVALO_LEITURA_MS) {
    ultimaLeitura = agora;
    atualizarTemperatura();
  }
}
