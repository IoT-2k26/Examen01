
# ==========================================================
# PROYECTO: MONITOREO DE UN INVERNADERO
# Raspberry Pi 5 + Python + MongoDB
# Sin convertidor MCP3008
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
PIN_DHT = 4                 # DHT22: humedad y temperatura ambiente
PIN_SUELO = 6               # Sensor digital de suelo
PIN_AGUA = 5                # Sensor digital de nivel de agua
PIN_TEMPERATURA = 24        # Alarma digital de temperatura
PIN_LUZ = 25                # Sensor digital de luz

# LED RGB
PIN_ROJO = 17
PIN_VERDE = 27
PIN_AZUL = 22

# Rele de la bomba
PIN_RELE = 12

DISPOSITIVO = "RaspberryPi5-Invernadero-01"
INTERVALO = 5


# ==========================================================
# 2. PARAMETROS DEL INVERNADERO
# ==========================================================

TEMP_MIN = 15
TEMP_MAX = 30

HUMEDAD_AIRE_MIN = 50
HUMEDAD_AIRE_MAX = 85

# Umbral del modulo digital de temperatura:
# Ajustarlo fisicamente en el modulo, si dispone de ajuste.
# GPIO24 solo informa si se activo la alarma digital.

# Cambiar a False si el modulo activa su salida con nivel bajo.
SUELO_SECO_ACTIVO_EN_ALTO = True
AGUA_BAJA_ACTIVA_EN_ALTO = True
POCA_LUZ_ACTIVA_EN_ALTO = True
ALARMA_TEMP_ACTIVA_EN_ALTO = True

# Reles habitualmente disponibles en modulos activos en bajo.
# Cambiar a True si tu rele se activa con nivel alto.
RELE_ACTIVO_EN_ALTO = False

# Seguridad: la bomba se activa unicamente cuando el suelo
# esta seco y hay agua suficiente.
# El control de riego es automatico.


# ==========================================================
# 3. CONFIGURACION DE MONGODB
# ==========================================================

MONGO_URI = "mongodb://localhost:27017/"
BASE_DATOS = "invernadero"
COLECCION = "mediciones"


# ==========================================================
# 4. FUNCIONES AUXILIARES
# ==========================================================

def crear_entrada(pin, activo_en_alto):
    """Crea una entrada digital con la polaridad configurada."""
    return DigitalInputDevice(
        pin,
        pull_up=False,
        active_state=activo_en_alto
    )


def limitar(valor, minimo, maximo):
    return max(minimo, min(valor, maximo))


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

def leer_sensores():
    """
    Las entradas digitales no proporcionan porcentajes
    analogicos. Cada modulo indica un estado o umbral.
    """

    return {
        "suelo_seco": suelo_sensor.is_active,
        "agua_baja": agua_sensor.is_active,
        "poca_luz": luz_sensor.is_active,
        "alarma_temperatura": temperatura_sensor.is_active,
    }


def leer_dht():
    """Lee temperatura y humedad del DHT22."""
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


def controlar_riego(suelo_seco, agua_baja):
    """
    Enciende la bomba si el sensor indica suelo seco
    y el sensor de agua NO indica nivel bajo.
    """

    activar = suelo_seco and not agua_baja

    if activar:
        rele.on()
    else:
        rele.off()

    return activar


def controlar_indicador(poca_luz, agua_baja, suelo_seco,
                        alarma_temperatura):
    # Prioridad 1: poca luz
    if poca_luz:
        mostrar_color("violeta")
        return "Violeta: poca luz"

    # Prioridad 2: nivel de agua bajo
    if agua_baja:
        mostrar_color("rojo")
        return "Rojo: nivel de agua bajo"

    # Prioridad 3: alarma digital de temperatura
    if alarma_temperatura:
        mostrar_color("rojo")
        return "Rojo: alarma de temperatura"

    # Prioridad 4: suelo seco y riego solicitado
    if suelo_seco:
        mostrar_color("azul")
        return "Azul: riego solicitado"

    mostrar_color("verde")
    return "Verde: condiciones normales"


# ==========================================================
# 7. INICIALIZACION DE HARDWARE
# ==========================================================

# Entradas digitales
suelo_sensor = crear_entrada(
    PIN_SUELO, SUELO_SECO_ACTIVO_EN_ALTO
)

agua_sensor = crear_entrada(
    PIN_AGUA, AGUA_BAJA_ACTIVA_EN_ALTO
)

luz_sensor = crear_entrada(
    PIN_LUZ, POCA_LUZ_ACTIVA_EN_ALTO
)

temperatura_sensor = crear_entrada(
    PIN_TEMPERATURA, ALARMA_TEMP_ACTIVA_EN_ALTO
)

# DHT22
dht = adafruit_dht.DHT22(
    board.D4,
    use_pulseio=False
)

# LED RGB: se supone catodo comun
led_rojo = LED(PIN_ROJO)
led_verde = LED(PIN_VERDE)
led_azul = LED(PIN_AZUL)

# Rele: inicialmente apagado
rele = OutputDevice(
    PIN_RELE,
    active_high=RELE_ACTIVO_EN_ALTO,
    initial_value=False
)


# ==========================================================
# 8. CONEXION A MONGODB
# ==========================================================

cliente = None
coleccion = None

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
    print("El monitoreo continuara sin guardar registros.")


# ==========================================================
# 9. CICLO PRINCIPAL
# ==========================================================

try:
    print("Iniciando monitoreo del invernadero...")
    print("Intervalo:", INTERVALO, "segundos")

    while True:
        fecha = datetime.now().astimezone()

        # Lecturas digitales
        estados = leer_sensores()

        suelo_seco = estados["suelo_seco"]
        agua_baja = estados["agua_baja"]
        poca_luz = estados["poca_luz"]
        alarma_temperatura = estados["alarma_temperatura"]

        # Lecturas del DHT22
        temperatura_aire, humedad_aire = leer_dht()

        estado_temperatura = evaluar_temperatura(
            temperatura_aire
        )

        estado_humedad_aire = evaluar_humedad_aire(
            humedad_aire
        )

        # Control de bomba y LED
        bomba_activa = controlar_riego(
            suelo_seco,
            agua_baja
        )

        estado_riego = evaluar_riego(
            suelo_seco,
            agua_baja
        )

        estado_rgb = controlar_indicador(
            poca_luz,
            agua_baja,
            suelo_seco,
            alarma_temperatura
        )

        # Mostrar resultados
        print("\n========== INVERNADERO ==========")
        print("Fecha:", fecha.isoformat())

        print("Temperatura DHT22:", temperatura_aire, "C")
        print("Humedad del aire:", humedad_aire, "%")
        print("Estado temperatura:", estado_temperatura)
        print("Estado humedad aire:", estado_humedad_aire)

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

finally:
    # Apagar la bomba y los indicadores al finalizar
    rele.off()
    apagar_rgb()

    # Liberar recursos
    dht.exit()

    suelo_sensor.close()
    agua_sensor.close()
    luz_sensor.close()
    temperatura_sensor.close()

    led_rojo.close()
    led_verde.close()
    led_azul.close()

    rele.close()

    if cliente is not None:
        cliente.close()

    print("Programa finalizado.")