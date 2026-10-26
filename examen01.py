#!/usr/bin/env python3
# ==========================================================
# MONITOREO DE UN INVERNADERO - Raspberry Pi 5 + MongoDB
# Version corregida y organizada con clases (POO)
# ==========================================================

from datetime import datetime
from time import sleep

import adafruit_dht
import board
from gpiozero import DigitalInputDevice, DistanceSensor, LED, OutputDevice
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# ==========================================================
# 1. CONFIGURACION (GPIO en numeracion BCM)
# ==========================================================
PIN_SUELO = 6          # humedad de suelo (salida digital DO)
PIN_LLUVIA = 5         # sensor de agua -> SOLO para detectar lluvia
PIN_TEMP_ALARMA = 24   # modulo de temperatura (salida digital)
PIN_LUZ = 25           # modulo de luz (salida digital)

PIN_ROJO, PIN_VERDE, PIN_AZUL = 17, 27, 22
PIN_RELE = 12

# Nivel del tanque: sensor ultrasonico HC-SR04 (PINES PROPUESTOS, libres)
PIN_TRIG = 23
PIN_ECHO = 18          # OJO: el ECHO sale a 5V -> usar divisor de voltaje

# El DHT esta en GPIO4 (board.D4). Si da "unplausible data" pruebe el otro modelo.
DHT_MODELO = "DHT11"   # "DHT11" o "DHT22"

DISPOSITIVO = "RaspberryPi5-Invernadero-01"
INTERVALO = 5

# --- Polaridad de las entradas (verificar con la prueba de cada sensor) ---
SUELO_SECO_ACTIVO_EN_ALTO = True
LLUVIA_ACTIVA_EN_ALTO = True
POCA_LUZ_ACTIVA_EN_ALTO = True
ALARMA_TEMP_ACTIVA_EN_ALTO = True
RELE_ACTIVO_EN_ALTO = False   # muchos modulos de rele son activos en bajo

# --- Parametros del cultivo (tomate). Validar con las fuentes del examen ---
TEMP_MIN, TEMP_MAX = 18.0, 27.0
HUMEDAD_AIRE_MIN, HUMEDAD_AIRE_MAX = 60.0, 80.0

# --- Tanque de agua lluvia ---
CAPACIDAD_TANQUE_L = 480
ALTURA_TANQUE_CM = 100        # MEDIR la altura real del contenedor
SIMULAR_TANQUE = True         # True = sin sensor ultrasonico (simulacion)
NIVEL_MINIMO_PCT = 10.0       # por debajo de esto no se riega
NIVEL_LLENO_PCT = 95.0        # por encima de esto no se capta mas agua
LITROS_POR_CICLO_RIEGO = 5    # simulacion: consumo por ciclo de riego
LITROS_POR_CICLO_LLUVIA = 8   # simulacion: ingreso por ciclo de lluvia

# --- MongoDB (Docker Desktop en Windows) ---
MONGO_URI = "mongodb://10.90.30.79:27017/"
BASE_DATOS = "invernadero"
COLECCION = "mediciones"


# ==========================================================
# 2. CLASES
# ==========================================================
class SensorDHT:
    """Temperatura y humedad del aire, con reintentos."""

    def __init__(self, modelo):
        clase = adafruit_dht.DHT11 if modelo == "DHT11" else adafruit_dht.DHT22
        self.sensor = clase(board.D4, use_pulseio=False)
        self.ultima = (None, None)

    def leer(self, reintentos=4):
        for _ in range(reintentos):
            try:
                t = self.sensor.temperature
                h = self.sensor.humidity
                if t is not None and h is not None:
                    self.ultima = (round(float(t), 1), round(float(h), 1))
                    return self.ultima
            except (RuntimeError, OSError) as error:
                # Errores normales del DHT (checksum, datos incompletos...)
                print("  DHT reintento:", error)
            sleep(2)  # el DHT necesita >= 1-2 s entre lecturas
        return self.ultima  # ultimo valor valido (o None, None al inicio)

    def cerrar(self):
        self.sensor.exit()


class TanqueAgua:
    """Nivel de un contenedor: ultrasonico real o simulado."""

    def __init__(self, capacidad_l, altura_cm, trig, echo, simulado):
        self.capacidad_l = capacidad_l
        self.altura_cm = altura_cm
        self.simulado = simulado
        self._litros_sim = capacidad_l * 0.5
        self._sensor = None
        if not simulado:
            self._sensor = DistanceSensor(
                echo=echo, trigger=trig, max_distance=altura_cm / 100
            )

    def porcentaje(self):
        if self.simulado:
            return round(self._litros_sim / self.capacidad_l * 100, 1)
        # El sensor va en la tapa: nivel = altura - distancia al agua
        nivel_cm = self.altura_cm - self._sensor.distance * 100
        pct = max(0.0, min(100.0, nivel_cm / self.altura_cm * 100))
        return round(pct, 1)

    def litros(self):
        return round(self.porcentaje() / 100 * self.capacidad_l, 1)

    def simular_cambio(self, delta_l):
        if self.simulado:
            self._litros_sim = max(
                0.0, min(self.capacidad_l, self._litros_sim + delta_l)
            )

    def cerrar(self):
        if self._sensor is not None:
            self._sensor.close()


class Invernadero:
    def __init__(self):
        self.entradas = {}
        self.leds = {}
        self.rele = None
        self.dht = None
        self.tanque = None
        self.cliente = None
        self.coleccion = None

    # ---------- Hardware ----------
    @staticmethod
    def _entrada(pin, activo_en_alto):
        # pull_up=None + active_state: respeta la polaridad configurada.
        return DigitalInputDevice(pin, pull_up=None, active_state=activo_en_alto)

    def iniciar_hardware(self):
        self.entradas = {
            "suelo": self._entrada(PIN_SUELO, SUELO_SECO_ACTIVO_EN_ALTO),
            "lluvia": self._entrada(PIN_LLUVIA, LLUVIA_ACTIVA_EN_ALTO),
            "luz": self._entrada(PIN_LUZ, POCA_LUZ_ACTIVA_EN_ALTO),
            "alarma_temp": self._entrada(PIN_TEMP_ALARMA, ALARMA_TEMP_ACTIVA_EN_ALTO),
        }
        self.leds = {
            "rojo": LED(PIN_ROJO),
            "verde": LED(PIN_VERDE),
            "azul": LED(PIN_AZUL),
        }
        self.rele = OutputDevice(
            PIN_RELE, active_high=RELE_ACTIVO_EN_ALTO, initial_value=False
        )
        self.dht = SensorDHT(DHT_MODELO)
        self.tanque = TanqueAgua(
            CAPACIDAD_TANQUE_L, ALTURA_TANQUE_CM, PIN_TRIG, PIN_ECHO, SIMULAR_TANQUE
        )
        self.apagar_rgb()

    def iniciar_mongo(self):
        try:
            self.cliente = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            self.cliente.admin.command("ping")
            self.coleccion = self.cliente[BASE_DATOS][COLECCION]
            print("Conexion con MongoDB correcta.")
        except PyMongoError as error:
            print("No se pudo conectar con MongoDB:", error)
            print("El monitoreo continuara sin guardar registros.")
            if self.cliente is not None:
                self.cliente.close()
            self.cliente = None
            self.coleccion = None

    # ---------- LED RGB (catodo comun) ----------
    def apagar_rgb(self):
        for led in self.leds.values():
            led.off()

    def mostrar_color(self, color):
        self.apagar_rgb()
        if color == "violeta":
            self.leds["rojo"].on()
            self.leds["azul"].on()
        else:
            self.leds[color].on()

    def controlar_indicador(self, poca_luz, nivel_bajo, alarma_temp, suelo_seco):
        if poca_luz:
            self.mostrar_color("violeta")
            return "Violeta: poca luz (luz UV simulada)"
        if nivel_bajo:
            self.mostrar_color("rojo")
            return "Rojo: nivel de agua bajo en el tanque"
        if alarma_temp:
            self.mostrar_color("rojo")
            return "Rojo: alarma de temperatura"
        if suelo_seco:
            self.mostrar_color("azul")
            return "Azul: riego solicitado"
        self.mostrar_color("verde")
        return "Verde: condiciones normales"

    # ---------- Evaluaciones ----------
    @staticmethod
    def evaluar(valor, minimo, maximo, nombre):
        if valor is None:
            return "Lectura no disponible"
        if valor < minimo:
            return f"{nombre} baja"
        if valor > maximo:
            return f"{nombre} alta"
        return f"{nombre} dentro del rango"

    # ---------- Un ciclo de monitoreo ----------
    def ciclo(self):
        fecha = datetime.now().astimezone()

        suelo_seco = self.entradas["suelo"].is_active
        llueve = self.entradas["lluvia"].is_active
        poca_luz = self.entradas["luz"].is_active
        alarma_temp = self.entradas["alarma_temp"].is_active

        temperatura, humedad = self.dht.leer()

        # Captacion de agua lluvia (la valvula/entrada se simula)
        nivel = self.tanque.porcentaje()
        captacion = llueve and nivel < NIVEL_LLENO_PCT
        if captacion:
            self.tanque.simular_cambio(LITROS_POR_CICLO_LLUVIA)

        # Distribucion de agua: suelo seco Y hay agua en el tanque
        nivel = self.tanque.porcentaje()
        nivel_bajo = nivel < NIVEL_MINIMO_PCT
        bomba = suelo_seco and not nivel_bajo
        if bomba:
            self.rele.on()
            self.tanque.simular_cambio(-LITROS_POR_CICLO_RIEGO)
        else:
            self.rele.off()

        estado_rgb = self.controlar_indicador(
            poca_luz, nivel_bajo, alarma_temp, suelo_seco
        )
        est_temp = self.evaluar(temperatura, TEMP_MIN, TEMP_MAX, "Temperatura")
        est_hum = self.evaluar(humedad, HUMEDAD_AIRE_MIN, HUMEDAD_AIRE_MAX, "Humedad aire")

        registro = {
            "dispositivo": DISPOSITIVO,
            "fecha": fecha,
            "temperatura_aire_c": temperatura,
            "humedad_aire_pct": humedad,
            "estado_temperatura": est_temp,
            "estado_humedad_aire": est_hum,
            "suelo_seco": suelo_seco,
            "lluvia_detectada": llueve,
            "captacion_activa": captacion,
            "nivel_tanque_pct": self.tanque.porcentaje(),
            "nivel_tanque_litros": self.tanque.litros(),
            "poca_luz": poca_luz,
            "alarma_temperatura_gpio24": alarma_temp,
            "bomba_activa": bomba,
            "estado_rgb": estado_rgb,
        }

        print("\n========== INVERNADERO ==========")
        print("Fecha:", fecha.isoformat())
        print("Temperatura:", temperatura, "C ->", est_temp)
        print("Humedad aire:", humedad, "% ->", est_hum)
        print("Suelo seco:", suelo_seco, "| Poca luz:", poca_luz)
        print("Lluvia:", llueve, "| Captacion:", captacion)
        print("Tanque:", registro["nivel_tanque_pct"], "% (",
              registro["nivel_tanque_litros"], "L )")
        print("Bomba:", bomba, "| RGB:", estado_rgb)

        if self.coleccion is not None:
            try:
                self.coleccion.insert_one(registro)
                print("Registro guardado en MongoDB.")
            except PyMongoError as error:
                print("Error al guardar en MongoDB:", error)

    def ejecutar(self):
        self.iniciar_hardware()
        self.iniciar_mongo()
        print("Iniciando monitoreo. Intervalo:", INTERVALO, "s")
        while True:
            self.ciclo()
            sleep(INTERVALO)

    def limpiar(self):
        if self.rele is not None:
            self.rele.off()
        self.apagar_rgb()
        recursos = list(self.entradas.values()) + list(self.leds.values())
        recursos.append(self.rele)
        for r in recursos:
            if r is not None:
                r.close()
        if self.tanque is not None:
            self.tanque.cerrar()
        if self.dht is not None:
            self.dht.cerrar()
        if self.cliente is not None:
            self.cliente.close()
        print("Programa finalizado.")


if __name__ == "__main__":
    app = Invernadero()
    try:
        app.ejecutar()
    except KeyboardInterrupt:
        print("\nMonitoreo detenido por el usuario.")
    except Exception as error:
        print("Error inesperado:", error)
    finally:
        app.limpiar()