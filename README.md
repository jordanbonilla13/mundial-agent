# Mundial Agent

Plataforma en Python/FastAPI para detectar, filtrar y seguir oportunidades de `value betting` en fútbol a partir de cuotas reales, señales ELO y gestión de stake controlada.

El objetivo del proyecto no es "apostar más", sino tomar decisiones más disciplinadas, registrar resultados y medir si la ventaja existe de verdad.

## Qué ofrece hoy

- Ingesta de cuotas desde `The Odds API` como proveedor principal.
- Soporte alternativo para `API-Football` y `SportsGameOdds`.
- Comparación contra `Pinnacle` como referencia de mercado.
- Modelo de probabilidad que combina mercado y ELO de forma conservadora.
- Cálculo de valor esperado, cuota mínima aceptable y stake por perfil.
- Tracking persistente de picks, resultados, ROI y `CLV`.
- Dashboard HTML para revisión rápida.
- Publicación opcional de picks en Telegram.

## Estado del proyecto

La base funcional ya existe y resuelve un flujo real de análisis. La siguiente etapa es profesionalizar tres áreas:

1. Arquitectura: separar la app monolítica en capas más claras.
2. Producto: mejorar UX, narrativa visual y coherencia del dashboard.
3. Confianza: reforzar testing, observabilidad y trazabilidad del modelo.

## Stack

- `Python`
- `FastAPI`
- `SQLite` o `PostgreSQL`
- `Requests`
- `Uvicorn`

## Estructura actual

```text
app/
  __init__.py            Paquete base de la aplicacion
  providers.py           Integraciones y adaptadores de proveedores
  schemas.py             Modelos Pydantic compartidos
  sports.py              Configuracion deportiva y helpers de contexto
main.py                  API FastAPI, HTML y orquestación principal
betting_model.py         Probabilidades, value, stake y scoring
tracking.py              Persistencia, métricas y liquidación de picks
elo.py                   Descarga y uso de ratings ELO
translations.py          Etiquetas y traducciones para la UI
form.py                  Lógica auxiliar de forma manual
analyzer.py              Módulo experimental no integrado
test_betting_model.py    Tests unitarios principales
render.yaml              Despliegue en Render
start-server.ps1         Arranque local en Windows
```

## Endpoints principales

```text
GET  /
GET  /status
GET  /cuotas
GET  /scores
GET  /hoy
GET  /informe-hoy
GET  /pronosticos
GET  /mis-apuestas
GET  /dashboard
GET  /tracking/picks
GET  /tracking/stats
GET  /tracking/aprendizaje
GET  /tracking/dashboard-data
POST /tracking/liquidar-auto
POST /tracking/picks/{pick_id}/resultado
```

## Puesta en marcha

1. Crear entorno virtual:

```bash
python -m venv venv
```

2. Activarlo en Windows:

```bash
venv\Scripts\activate
```

3. Instalar dependencias:

```bash
pip install -r requirements.txt
```

4. Copiar configuración:

```bash
copy .env.example .env
```

5. Arrancar el servidor:

```bash
uvicorn main:app --reload
```

También puedes usar:

```powershell
.\start-server.ps1
```

## Variables de entorno

Configuración mínima:

```env
ODDS_PROVIDER=the_odds_api
ODDS_API_KEY=tu_api_key
REFERENCE_BOOKMAKER=Pinnacle
```

Configuración opcional para Telegram:

```env
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
TELEGRAM_AUTOPUBLISH_ENABLED=true
```

La plantilla completa está en [.env.example](C:/mundial-agent/mundial-agent/.env.example:1).

## Flujo recomendado

1. Revisar picks del día en `/informe-hoy`.
2. Guardar recomendaciones cuando quieras trackearlas.
3. Registrar solo las apuestas realmente ejecutadas.
4. Liquidar resultados manual o automáticamente.
5. Revisar `ROI`, `hit rate` y `CLV` en el dashboard.

## Modos de análisis

- `comparador`: usa Pinnacle como referencia y busca mejores cuotas en otras casas.
- `pinnacle`: analiza directamente la cuota de Pinnacle.

El modo recomendado es `comparador`, porque refleja mejor un flujo real de captura de precio.

## Perfil de riesgo

El sistema incluye perfiles de stake con filtros y exposición diferentes:

- `conservador`
- `moderado`
- `agresivo`
- `alto_riesgo`

Esto permite separar claramente una estrategia disciplinada de una estrategia más especulativa.

## Despliegue

El proyecto incluye [render.yaml](C:/mundial-agent/mundial-agent/render.yaml:1) para desplegarlo como servicio web en Render.

Comando de arranque en producción:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## Riesgos y limitaciones actuales

- La app está muy concentrada en [`main.py`](C:/mundial-agent/mundial-agent/main.py:1).
- Parte de la UI HTML está acoplada a la lógica de negocio.
- Hay mercados y deportes con soporte parcial según proveedor.
- El proyecto tiene tests útiles, pero aún no cubre todas las rutas críticas.
- `analyzer.py` sigue siendo un módulo experimental sin integrar.

## Roadmap de profesionalización

### Fase 1

- Corregir detalles de presentación, naming y consistencia.
- Limpiar textos y mensajes visibles.
- Reducir deuda de estructura en la capa API.

### Fase 2

- Separar `routes`, `services`, `providers`, `schemas` y `repositories`.
- Extraer los adaptadores de Odds API, API-Football y SportsGameOdds.
- Unificar validación y respuestas con modelos Pydantic.

### Fase 3

- Mejorar el dashboard con una UI más sobria y más premium.
- Añadir observabilidad, logging estructurado y health metrics.
- Incorporar backtesting y reporting histórico más serio.

### Fase 4

- Multiusuario, autenticación y configuración persistente.
- Panel de administración y gestión avanzada de bankroll.
- Evaluación del proyecto como producto SaaS especializado.

## Próximo paso recomendado

Si queremos subir realmente el nivel del repositorio, el siguiente paso más rentable es dividir [`main.py`](C:/mundial-agent/mundial-agent/main.py:1) por dominios y dejar la lógica de presentación fuera de la capa principal.
