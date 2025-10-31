// src/main.cpp

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL345_U.h>
#include <WiFi.h>
#include <PubSubClient.h> // <-- Librería MQTT 

// --------- CONFIG ---------
// ¡¡IMPORTANTE: CAMBIA ESTO EN CADA MICROCONTROLADOR!!
const char* DEVICE_ID = "sensor_004"; // Debe coincidir con 'device_serial' en Supabase

#define SDA_PIN 21
#define SCL_PIN 22
#define SERIAL_BAUD 115200
// #define SAMPLE_INTERVAL_MS 50 // ~20Hz (Ajusta según SAMPLE_INTERVAL de Python)
#define SAMPLE_INTERVAL_MS 5 // ~200Hz (Ajusta según SAMPLE_INTERVAL de Python)
// --- CONFIG WIFI ---
const char* WIFI_SSID = "Fibertel WiFi398 2.4GHz";     // <--- TU WIFI
const char* WIFI_PASS = "0041782371"; // <--- TU CONTRASEÑA

// --- CONFIG MQTT ---
// IP de tu PC ejecutando Docker/Mosquitto. O "broker.emqx.io" para pruebas [24, 28]
const char* MQTT_BROKER = "192.168.0.24";  //ipconfig en consola
const int MQTT_PORT = 1883;

//     docker run -d --name mosquitto_broker -p 1883:1883 -p 9001:9001 `
// >>   -v "${PWD}\config:/mosquitto/config" `
// >>   -v "${PWD}\data:/mosquitto/data" `
// >>   -v "${PWD}\log:/mosquitto/log" `
// >>   eclipse-mosquitto:latest

// Tópicos (se construirán automáticamente)
char TELEMETRY_TOPIC[100];
char COMMAND_TOPIC[100];
// --------------------------

Adafruit_ADXL345_Unified accel = Adafruit_ADXL345_Unified(12345);
WiFiClient espClient;
PubSubClient client(espClient); // [44]

// Flag de estado, 'volatile' porque es modificada por la interrupción de MQTT (callback)
volatile bool streaming = false; 
unsigned long lastReconnectAttempt = 0;
const unsigned long ID_PRINT_INTERVAL = 10000; // ms

void setup_wifi() {
    delay(10);
    Serial.println();
    Serial.print("Conectando a ");
    Serial.println(WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASS); // [27, 28]
    while (WiFi.status()!= WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nWiFi conectado.");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
}

/**
 * @brief Callback de MQTT. Se llama CADA vez que llega un mensaje
 * en un tópico al que estamos suscritos (ej. /comando).
 * 
 */
void mqtt_callback(char* topic, byte* payload, unsigned int length) {
    // Convertir payload a String
    payload[length] = '\0'; // Asegurar fin de string
    String message = (char*)payload;
    message.toUpperCase();

    Serial.print("Comando MQTT recibido: ");
    Serial.println(message);

    if (message == "START") {
        streaming = true;
        Serial.println("ACK,START (MQTT)");
    } else if (message == "STOP") {
        streaming = false;
        Serial.println("ACK,STOP (MQTT)");
    }
}

/**
 * @brief Reconexión a MQTT (Lógica NO-BLOQUEANTE).
 * Usa millis() para no congelar el loop si el broker está caído.
 * [45, 46, 47, 48]
 */
void reconnect_mqtt() {
    long now = millis();
    // Solo intentar reconectar cada 5 segundos
    if (now - lastReconnectAttempt > 5000) {
        lastReconnectAttempt = now;
        if (!client.connected()) {
            Serial.print("Intentando conexión MQTT...");
            // Intentar conectar con el Device ID
            if (client.connect(DEVICE_ID)) {
                Serial.println("conectado.");
                // Volver a suscribirse al tópico de comandos
                client.subscribe(COMMAND_TOPIC); // [27, 41, 42]
                Serial.print("Suscrito a: ");
                Serial.println(COMMAND_TOPIC);
            } else {
                Serial.print("falló, rc=");
                Serial.print(client.state()); // [27, 45, 49]
                Serial.println(" reintentando en 5 seg.");
            }
        }
    }
}

// --- Lógica de Comandos Seriales ---
String read_line_from_serial() {
  static String buf = "";
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      String line = buf;
      buf = "";
      line.trim();
      return line;
    } else {
      buf += c;
    }
  }
  return String();
}

void handle_command(const String &cmd) {
  if (cmd.startsWith("CMD,")) {
    String arg = cmd.substring(4);
    arg.trim();
    if (arg.equalsIgnoreCase("START")) {
      streaming = true;
      Serial.println("ACK,START (Serial)");
    } else if (arg.equalsIgnoreCase("STOP")) {
      streaming = false;
      Serial.println("ACK,STOP (Serial)");
    }
  }
}
// ---------------------------------------------------
void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(150);
    Serial.printf("\n=== ADXL345 Logger HÍBRIDO (Device: %s) ===\n", DEVICE_ID);

    // Construir los nombres de tópicos únicos
    snprintf(TELEMETRY_TOPIC, 100, "dispositivo/%s/telemetria", DEVICE_ID);
    snprintf(COMMAND_TOPIC, 100, "dispositivo/%s/comando", DEVICE_ID);

    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(400000);

    if (!accel.begin()) {
        Serial.println("ERROR: ADXL345 no detectado.");
        while (true) { delay(1000); }
    }

    setup_wifi();
    client.setServer(MQTT_BROKER, MQTT_PORT); // [27, 28]
    client.setCallback(mqtt_callback); // [27, 28, 41, 42]
    
    lastReconnectAttempt = 0;
    
    // Imprime ID al inicio para el modo Serial
    Serial.printf("ID,%s\n", DEVICE_ID);
}


// --- VARIABLES DEL FILTRO PASA-ALTO ---
// 'alpha' controla qué tan "lento" es el filtro. 
// Un valor más alto (ej. 0.9) filtra más, dejando solo cambios muy rápidos.
// Un valor más bajo (ej. 0.5) deja pasar más movimiento lento.
const float alpha = 0.95; 
float gravity_x = 0;
float gravity_y = 0;
float gravity_z = 9.8; // Asumir que empieza plano
// ----------------------------------------
void loop() {
    // MANTENIMIENTO DE CONEXIÓN (SIEMPRE) ---
    if (WiFi.status()!= WL_CONNECTED) {
        setup_wifi(); // Reconectar si se pierde el WiFi
    }
    
    if (!client.connected()) {
        reconnect_mqtt(); // Manejar reconexión MQTT (no bloqueante) [27, 45, 49]
    }
    
    // FUNDAMENTAL: Escuchar por mensajes MQTT entrantes
    client.loop(); // [27, 45, 46, 4, 49, 50, 43]

    // MANEJO DE COMANDOS SERIALES (SIEMPRE) ---
    String ln = read_line_from_serial();
    if (ln.length() > 0) {
        handle_command(ln);
    }

  if (streaming) {
    sensors_event_t event;
    accel.getEvent(&event); // m/s^2

    unsigned long ts_us = micros();

    // --- APLICAR FILTRO ---
    // Calcular la gravedad (componente lenta) con un filtro pasa-bajo
    gravity_x = (alpha * gravity_x) + (1.0 - alpha) * event.acceleration.x;
    gravity_y = (alpha * gravity_y) + (1.0 - alpha) * event.acceleration.y;
    gravity_z = (alpha * gravity_z) + (1.0 - alpha) * event.acceleration.z;

    // Restar la gravedad para obtener la aceleración lineal (componente rápida)
    float linear_ax = event.acceleration.x - gravity_x;
    float linear_ay = event.acceleration.y - gravity_y;
    float linear_az = event.acceleration.z - gravity_z;
    // ----------------------


    // Crear payload JSON (¡usando los valores 'linear_a*')
    char payload[256];
    snprintf(payload, sizeof(payload), 
             "{\"device_id\": \"%s\", \"ts_us\": %lu, \"ax\": %.3f, \"ay\": %.3f, \"az\": %.3f}",
             DEVICE_ID,
             ts_us,
             linear_ax, // <-- Usar el valor filtrado
             linear_ay, // <-- Usar el valor filtrado
             linear_az  // <-- Usar el valor filtrado
             );
    // char payload[256];
    // snprintf(payload, sizeof(payload), 
    //          "{\"device_id\": \"%s\", \"ts_us\": %lu, \"ax\": %.3f, \"ay\": %.3f, \"az\": %.3f}",
    //          DEVICE_ID,
    //          ts_us,
    //          event.acceleration.x, // <-- Usa el valor en crudo
    //          event.acceleration.y, // <-- Usa el valor en crudo
    //          event.acceleration.z  // <-- Usa el valor en crudo
    //          );
    // --- PUBLICACIÓN HÍBRIDA (MQTT y Serial) ---
    client.publish(TELEMETRY_TOPIC, payload);
    Serial.println(payload);

    delay(SAMPLE_INTERVAL_MS); // Controlar la frecuencia
  }
}