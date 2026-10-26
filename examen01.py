
# ==========================================
# PROYECTO: MONITOREO DE UN INVERNADERO
# Raspberry Pi 5 + Python
# ==========================================

from gpiozero import MCP3008, LED, DigitalInputDevice
from time import sleep
from datetime import datetime
import adafruit_dht
import board
from pymongo import MongoClient
from pymongo.errors import PyMongoError


# 1. CONFIGURACION DE PINES
CANAL_TEMPERATURA = 0
CANAL_LUZ = 1
CANAL_SUELO = 2
CANAL_AGUA = 3

PIN_DHT = board.D4
PIN_LLUVIA = 17

PIN_ROJO = 22
PIN_VERDE = 27
PIN_AZUL = 18

DISPOSITIVO = "RaspberryPi5-Invernadero-01"
INTERVALO = 5


# 2. VALORES DE REFERENCIA
TEMP_MIN = 15
TEMP_MAX = 30

HUMEDAD_AIRE_MIN = 50
HUMEDAD_AIRE_MAX = 85

LUZ_MINIMA = 30

# Valores de ejemplo: calibrar con el sensor real.
SUELO_SECO = 800
SUELO_HUMEDO = 300
UMBRAL_RIEGO = 30

NIVEL_MINIMO = 15

# True si el sensor de lluvia se activa con nivel bajo.
LLUVIA_ACTIVA_EN_BAJO = True

MONGO_URI = "mongodb://localhost:27017/"
BASE_DATOS = "invernadero"
COLECCION = "mediciones"


# 3. FUNCIONES DE CONVERSION
def limitar(valor, minimo, maximo):
    return max(minimo, min(valor, maximo))


def convertir_porcentaje(valor, seco, humedo):
    if seco == humedo:
        return 0

    porcentaje = (seco - valor) * 100 / (seco - humedo)
    return round(limitar(porcentaje, 0, 100), 1)


def leer_temperatura():
    # Para LM35: voltaje en V x 100 = grados Celsius.
    voltaje = temperatura_adc.voltage
    return round(voltaje * 100, 1)


def leer_luz():
    # Porcentaje relativo estimado; no son lux.
    lectura = luz_adc.value * 100
    return round(lectura, 1)


def leer_suelo():
    lectura = suelo_adc.value * 1023
    return convertir_porcentaje(
        lectura, SUELO_SECO, SUELO_HUMEDO
    )


def leer_nivel_agua():
    lectura = agua_adc.value * 1023
    nivel = (
        (lectura - AGUA_VACIO) * 100
        / (AGUA_LLENO - AGUA_VACIO)
    )
    return round(limitar(nivel, 0, 100), 1)


# 4. CONFIGURACION DE SENSORES Y SALIDAS
temperatura_adc = MCP3008(channel=CANAL_TEMPERATURA)
luz_adc = MCP3008(channel=CANAL_LUZ)
suelo_adc = MCP3008(channel=CANAL_SUELO)
agua_adc = MCP3008(channel=CANAL_AGUA)

# EJEMPLOS: sustituir por las lecturas reales de calibracion.
AGUA_VACIO = 100
AGUA_LLENO = 900

dht = adafruit_dht.DHT22(PIN_DHT, use_pulseio=False)

lluvia_sensor = DigitalInputDevice(
    PIN_LLUVIA,
    pull_up=False,
    active_state=not LLUVIA_ACTIVA_EN_BAJO
)

# El LED RGB funciona como indicador unico.
# Se supone que es de catodo comun.
led_rojo = LED(PIN_ROJO)
led_verde = LED(PIN_VERDE)
led_azul = LED(PIN_AZUL)


# 5. CONEXION CON MONGODB
coleccion = None
cliente = None

try:
    cliente = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=3000
    )
    cliente.admin.command("ping")
    base_datos = cliente[BASE_DATOS]
    coleccion = base_datos[COLECCION]
    print("Conexion con MongoDB correcta.")

except PyMongoError as error:
    print("No se pudo conectar con MongoDB:", error)
    print("El programa continuara mostrando las lecturas.")


# 6. CONTROL DEL LED RGB Y DEL RIEGO SIMULADO
def apagar_rgb():
    led_rojo.off()
    led_verde.off()
    led_azul.off()


def mostrar_color(color):
    apagar_rgb()

    if color == "violeta":
        led_rojo.on()
        led_azul.on()

    elif color == "verde":
        led_verde.on()

    elif color == "azul":
        led_azul.on()

    elif color == "rojo":
        led_rojo.on()


def evaluar_riego(humedad_suelo, nivel_agua):
    if nivel_agua <= NIVEL_MINIMO:
        return "Riego detenido: nivel de agua bajo"

    if humedad_suelo < UMBRAL_RIEGO:
        return "Riego ACTIVADO (simulado)"

    return "Riego apagado: suelo humedo"


def controlar_indicador(luz, humedad_suelo, nivel_agua):
    # Prioridad 1: luz violeta cuando hay poca luz ambiental.
    if luz < LUZ_MINIMA:
        mostrar_color("violeta")
        return "Violeta: poca luz ambiental"

    # Prioridad 2: advertir que no hay agua suficiente.
    if nivel_agua <= NIVEL_MINIMO:
        mostrar_color("rojo")
        return "Rojo: nivel de agua bajo"

    # Prioridad 3: indicar que el riego esta activo.
    if humedad_suelo < UMBRAL_RIEGO:
        mostrar_color("azul")
        return "Azul: riego simulado activo"

    # Estado normal.
    mostrar_color("verde")
    return "Verde: condiciones normales"


def controlar_lluvia():
    return lluvia_sensor.is_active


# 7. CICLO PRINCIPAL
try:
    print("Iniciando monitoreo del invernadero...")

    while True:
        temperatura = leer_temperatura()

        try:
            temperatura_aire = dht.temperature
            humedad_aire = dht.humidity

        except RuntimeError:
            # El DHT puede fallar ocasionalmente una lectura.
            temperatura_aire = None
            humedad_aire = None

        luz = leer_luz()
        humedad_suelo = leer_suelo()
        nivel_agua = leer_nivel_agua()
        lluvia = controlar_lluvia()

        estado_riego = evaluar_riego(
            humedad_suelo,
            nivel_agua
        )

        estado_rgb = controlar_indicador(
            luz,
            humedad_suelo,
            nivel_agua
        )

        if temperatura < TEMP_MIN:
            estado_temperatura = "Temperatura baja"
        elif temperatura > TEMP_MAX:
            estado_temperatura = "Temperatura alta"
        else:
            estado_temperatura = "Temperatura dentro del rango"

        if humedad_aire is None:
            estado_humedad_aire = "Lectura no disponible"
        elif humedad_aire < HUMEDAD_AIRE_MIN:
            estado_humedad_aire = "Humedad del aire baja"
        elif humedad_aire > HUMEDAD_AIRE_MAX:
            estado_humedad_aire = "Humedad del aire alta"
        else:
            estado_humedad_aire = (
                "Humedad del aire dentro del rango"
            )

        fecha = datetime.now().astimezone()

        print("\n========== INVERNADERO ==========")
        print("Fecha:", fecha.isoformat())
        print("Temperatura LM35:", temperatura, "C")
        print("Temperatura DHT22:", temperatura_aire)
        print("Humedad del aire:", humedad_aire, "%")
        print("Estado temperatura:", estado_temperatura)
        print("Estado humedad aire:", estado_humedad_aire)
        print("Luz:", luz, "%")
        print("Estado del indicador RGB:", estado_rgb)
        print("Humedad del suelo:", humedad_suelo, "%")
        print("Nivel del tanque:", nivel_agua, "%")
        print("Lluvia detectada:", lluvia)
        print("Estado del riego:", estado_riego)

        registro = {
            "dispositivo": DISPOSITIVO,
            "fecha": fecha,
            "temperatura_lm35_c": temperatura,
            "temperatura_dht22_c": temperatura_aire,
            "humedad_aire_pct": humedad_aire,
            "estado_temperatura": estado_temperatura,
            "estado_humedad_aire": estado_humedad_aire,
            "luz_pct": luz,
            "estado_rgb": estado_rgb,
            "humedad_suelo_pct": humedad_suelo,
            "nivel_agua_pct": nivel_agua,
            "lluvia_detectada": lluvia,
            "captacion_agua": (
                "Lluvia detectada"
                if lluvia
                else "Sin lluvia detectada"
            ),
            "estado_riego": estado_riego
        }

        if coleccion is not None:
            try:
                coleccion.insert_one(registro)
                print("Registro guardado en MongoDB.")

            except PyMongoError as error:
                print("Error al guardar en MongoDB:", error)

        sleep(INTERVALO)

except KeyboardInterrupt:
    print("\nMonitoreo detenido por el usuario.")

finally:
    apagar_rgb()

    temperatura_adc.close()
    luz_adc.close()
    suelo_adc.close()
    agua_adc.close()

    dht.exit()
    lluvia_sensor.close()

    led_rojo.close()
    led_verde.close()
    led_azul.close()

    if cliente is not None:
        cliente.close()

    print("Programa finalizado.")
