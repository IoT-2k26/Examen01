
# ==========================================================
# PROYECTO: MONITOREO DE UN INVERNADERO
# Raspberry Pi 5 + Python + MongoDB en Docker Desktop
# Administracion remota mediante RealVNC Viewer
# ==========================================================

from gpiozero import DigitalInputDevice, LED, OutputDevice
from time import sleep
from datetime import datetime
import adafruit_dht
import board

from pymongo import MongoClient
from pymongo.errors import PyMongoError


# ==========================================================
# 1. CONFIGURACION DE GPIO (NUMERACION BCM)
# ==========================================================

# Sensores
PIN_DHT = 4
PIN_SUELO = 6
PIN_AGUA = 5
PIN_TEMPERATURA = 24
PIN_LUZ = 25

# LED RGB
PIN_ROJO = 17
PIN_VERDE = 27
PIN_AZUL = 22

# Rele de la bomba
PIN_RELE = 12

DISPOSITIVO = "RaspberryPi5-Invernadero-01"
INTERVALO = 5


# ==========================================================
# 2. PARAMETROS DEL SISTEMA
# ==========================================================

TEMP_MIN = 15.0
TEMP_MAX = 30.0

HUMEDAD_AIRE_MIN = 50.0
HUMEDAD_AIRE_MAX = 85.0

# Configuracion de polaridad de las entradas.
# True: el estado activo corresponde a nivel alto.
# False: el estado activo corresponde a nivel bajo.
SUELO_SECO_ACTIVO_EN_ALTO = True
AGUA_BAJA_ACTIVA_EN_ALTO = True
POCA_LUZ_ACTIVA_EN_ALTO = True
ALARMA_TEMP_ACTIVA_EN_ALTO = True

# Muchos modulos de rele son activos en nivel bajo.
# Verificar la polaridad del modulo antes de conectar la bomba.
RELE_ACTIVO_EN_ALTO = False

# Para un LED RGB de catodo comun, LED.on() activa el canal.
# Si el RGB es de anodo comun, se debe adaptar su control.


# ==========================================================
# 3. CONFIGURACION DE MONGODB
# ==========================================================

# MongoDB corre en Docker Desktop en Windows.
MONGO_URI = "mongodb://10.90.30.79:27017/"

BASE_DATOS = "invernadero"
COLECCION = "mediciones"


# ==========================================================
# 4. FUNCIONES AUXILIARES
# ==========================================================

def crear_entrada(pin, activo_en_alto):
    return DigitalInputDevice(
        pin,
        pull_up=False,
        active_state=activo_en_alto
    )


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


# ==========================================================
# 5. LECTURA DE SENSORES
# ==========================================================

def leer_sensores_digitales():
    return {
        "suelo_seco": suelo_sensor.is_active,
        "agua_baja": agua_sensor.is_active,
        "poca_luz": luz_sensor.is_active,
        "alarma_temperatura": temperatura_sensor.is_active,
    }


def leer_dht():
    try:
        temperatura = dht.temperature
        humedad = dht.humidity

        if temperatura is None or humedad is None:
            return None, None

        return round(temperatura, 1), round(humedad, 1)

    except (RuntimeError, OSError) as error:
        print("Lectura DHT22 no disponible:", error)
        return None, None


# ==========================================================
# 6. EVALUACION DE CONDICIONES
# ==========================================================

def evaluar_temperatura(temperatura):
    if temperatura is None:
        return "Lectura no disponible"

    if temperatura < TEMP_MIN:
        return "Temperatura baja"

    if temperatura > TEMP_MAX:
        return "Temperatura alta"

    return "Temperatura dentro del rango"


def evaluar_humedad_aire(humedad):
    if humedad is None:
        return "Lectura no disponible"

    if humedad < HUMEDAD_AIRE_MIN:
        return "Humedad del aire baja"

    if humedad > HUMEDAD_AIRE_MAX:
        return "Humedad del aire alta"

    return "Humedad del aire dentro del rango"


def evaluar_riego(suelo_seco, agua_baja):
    if agua_baja:
        return "Riego detenido: nivel de agua bajo"

    if suelo_seco:
        return "Riego ACTIVADO"

    return "Riego apagado: suelo no indica sequedad"


# ==========================================================
# 7. CONTROL DEL RELE
# ==========================================================

def controlar_riego(suelo_seco, agua_baja):
    # Seguridad basica: nunca solicitar riego con agua baja.
    activar = suelo_seco and not agua_baja

    if activar:
        rele.on()
    else:
        rele.off()

    return activar


# ==========================================================
# 8. CONTROL DEL LED RGB
# ==========================================================

def controlar_indicador(
    poca_luz,
    agua_baja,
    suelo_seco,
    alarma_temperatura
):
    # Prioridad 1: poca luz
    if poca_luz:
        mostrar_color("violeta")
        return "Violeta: poca luz ambiental"

    # Prioridad 2: nivel de agua bajo
    if agua_baja:
        mostrar_color("rojo")
        return "Rojo: nivel de agua bajo"

    # Prioridad 3: alarma de temperatura
    if alarma_temperatura:
        mostrar_color("rojo")
        return "Rojo: alarma de temperatura"

    # Prioridad 4: solicitud de riego
    if suelo_seco:
        mostrar_color("azul")
        return "Azul: riego solicitado"

    mostrar_color("verde")
    return "Verde: condiciones normales"


# ==========================================================
# 9. INICIALIZACION DEL HARDWARE
# ==========================================================

cliente = None
coleccion = None

# Declarar recursos antes del try para permitir limpieza segura.
suelo_sensor = None
agua_sensor = None
luz_sensor = None
temperatura_sensor = None
dht = None

led_rojo = None
led_verde = None
led_azul = None
rele = None

try:
    # Entradas digitales
    suelo_sensor = crear_entrada(
        PIN_SUELO,
        SUELO_SECO_ACTIVO_EN_ALTO
    )

    agua_sensor = crear_entrada(
        PIN_AGUA,
        AGUA_BAJA_ACTIVA_EN_ALTO
    )

    luz_sensor = crear_entrada(
        PIN_LUZ,
        POCA_LUZ_ACTIVA_EN_ALTO
    )

    temperatura_sensor = crear_entrada(
        PIN_TEMPERATURA,
        ALARMA_TEMP_ACTIVA_EN_ALTO
    )

    # DHT22 en GPIO4
    dht = adafruit_dht.DHT22(
        board.D4,
        use_pulseio=False
    )

    # LED RGB de catodo comun
    led_rojo = LED(PIN_ROJO)
    led_verde = LED(PIN_VERDE)
    led_azul = LED(PIN_AZUL)

    # Inicializar el rele apagado
    rele = OutputDevice(
        PIN_RELE,
        active_high=RELE_ACTIVO_EN_ALTO,
        initial_value=False
    )

    apagar_rgb()
    rele.off()

    # ======================================================
    # 10. CONEXION CON MONGODB EN WINDOWS
    # ======================================================

    try:
        cliente = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000
        )

        cliente.admin.command("ping")

        base_datos = cliente[BASE_DATOS]
        coleccion = base_datos[COLECCION]

        print("Conexion con MongoDB correcta.")
        print("Servidor:", MONGO_URI)
        print("Base de datos:", BASE_DATOS)
        print("Coleccion:", COLECCION)

    except PyMongoError as error:
        print("No se pudo conectar con MongoDB:", error)
        print("El monitoreo continuara sin guardar registros.")

        if cliente is not None:
            cliente.close()

        cliente = None
        coleccion = None

    # ======================================================
    # 11. CICLO PRINCIPAL
    # ======================================================

    print("Iniciando monitoreo del invernadero...")
    print("Intervalo:", INTERVALO, "segundos")

    while True:
        fecha = datetime.now().astimezone()

        # Leer entradas digitales
        estados = leer_sensores_digitales()

        suelo_seco = estados["suelo_seco"]
        agua_baja = estados["agua_baja"]
        poca_luz = estados["poca_luz"]
        alarma_temperatura = estados["alarma_temperatura"]

        # Leer DHT22
        temperatura_aire, humedad_aire = leer_dht()

        estado_temperatura = evaluar_temperatura(
            temperatura_aire
        )

        estado_humedad_aire = evaluar_humedad_aire(
            humedad_aire
        )

        # Control de riego
        bomba_activa = controlar_riego(
            suelo_seco,
            agua_baja
        )

        estado_riego = evaluar_riego(
            suelo_seco,
            agua_baja
        )

        # Control del indicador
        estado_rgb = controlar_indicador(
            poca_luz,
            agua_baja,
            suelo_seco,
            alarma_temperatura
        )

        # Mostrar resultados
        print("\n========== INVERNADERO ==========")
        print("Fecha:", fecha.isoformat())

        print("Temperatura ambiente:", temperatura_aire, "C")
        print("Humedad ambiente:", humedad_aire, "%")
        print("Estado temperatura:", estado_temperatura)
        print("Estado humedad:", estado_humedad_aire)

        print("Suelo seco detectado:", suelo_seco)
        print("Nivel de agua bajo:", agua_baja)
        print("Poca luz detectada:", poca_luz)
        print("Alarma temperatura GPIO24:", alarma_temperatura)

        print("Estado RGB:", estado_rgb)
        print("Estado del riego:", estado_riego)
        print("Bomba activa:", bomba_activa)

        # Documento para MongoDB
        registro = {
            "dispositivo": DISPOSITIVO,
            "fecha": fecha,

            "temperatura_aire_c": temperatura_aire,
            "humedad_aire_pct": humedad_aire,

            "estado_temperatura": estado_temperatura,
            "estado_humedad_aire": estado_humedad_aire,

            "suelo_seco_detectado": suelo_seco,
            "nivel_agua_bajo": agua_baja,
            "poca_luz_detectada": poca_luz,
            "alarma_temperatura_gpio24": alarma_temperatura,

            "estado_rgb": estado_rgb,
            "estado_riego": estado_riego,
            "bomba_activa": bomba_activa,
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

except Exception as error:
    print("Error inesperado:", error)

finally:
    # Apagar bomba e indicadores antes de liberar recursos
    if rele is not None:
        rele.off()

    if led_rojo is not None:
        led_rojo.off()

    if led_verde is not None:
        led_verde.off()

    if led_azul is not None:
        led_azul.off()

    # Liberar recursos
    for dispositivo in (
        suelo_sensor,
        agua_sensor,
        luz_sensor,
        temperatura_sensor,
        led_rojo,
        led_verde,
        led_azul,
        rele
    ):
        if dispositivo is not None:
            dispositivo.close()

    if dht is not None:
        dht.exit()

    if cliente is not None:
        cliente.close()

    print("Programa finalizado.")