# Mundial Agent

Plataforma en `Python` + `FastAPI` para detectar, filtrar y seguir oportunidades de `value betting` en futbol a partir de cuotas reales, señales de mercado, ratings ELO y control de stake.

El objetivo del proyecto no es "apostar mas", sino tomar decisiones mas disciplinadas, registrar resultados y comprobar con datos si la ventaja existe de verdad.

## Que hace hoy

- Ingesta de cuotas desde `The Odds API` como proveedor principal.
- Soporte alternativo para `API-Football` y `SportsGameOdds`.
- Comparacion contra `Pinnacle` como referencia de mercado.
- Modelo de probabilidad que mezcla señal de mercado, contexto deportivo y ELO.
- Scoring de picks con filtros de riesgo, exposicion y ranking operativo.
- Tracking persistente de picks, apuestas reales, bankroll, ROI y `CLV`.
- Dashboard HTML y vistas operativas para revisar picks, resultados y aprendizaje.
- Publicacion opcional de picks en Telegram con botones y registro historico.
- Resumenes premium con OpenAI para picks y auditorias diarias.
- Endpoints de calibracion y auditoria para medir calidad real del modelo.

## Estado actual

La base funcional ya existe y cubre un flujo real de analisis:

1. Descargar cuotas y resultados.
2. Generar picks segun mercado, perfil y modo operativo.
3. Guardar recomendaciones y apuestas ejecutadas.
4. Liquidar resultados manual o automaticamente.
5. Revisar metricas, aprendizaje, calibracion y auditoria.

La app sigue teniendo bastante logica concentrada en [`main.py`](./main.py), pero ya se ha empezado a extraer funcionalidad a modulos mas claros en `app/`.

## Stack

- `Python`
- `FastAPI`
- `Pydantic`
- `SQLite` o `PostgreSQL`
- `Requests`
- `Uvicorn`

## Estructura real del proyecto

```text
app/
  __init__.py              Paquete base
  ai_service.py            Integracion con OpenAI para narrativas y resúmenes
  audit.py                 Auditoria diaria del rendimiento publicado
  calibrated_scoring.py    Ajustes de scoring apoyados en calibracion historica
  calibration.py           Analisis de calibracion por liga, mercado, tier y casa
  engine.py                ForecastEngine y request model del flujo principal
  exposure.py              Limites de exposicion por evento, liga y mercado
  forecasting.py           Ranking, execution score y enriquecimiento de picks
  operating_mode.py        Modos operativos y limites de diversificacion
  providers.py             Integraciones y adaptadores de proveedores
  risk_controls.py         Politicas de riesgo y filtros aplicados a picks
  schemas.py               Modelos Pydantic de la API
  sports.py                Catalogo deportivo y helpers de contexto
  telegram_service.py      Formateo y cliente de Telegram
  ui.py                    CSS y helpers visuales del dashboard
main.py                    API FastAPI, HTML y orquestacion principal
betting_model.py           Probabilidades, value, stake, confianza y analisis
tracking.py                Persistencia, metricas, liquidacion y dashboards
elo.py                     Descarga y uso de ratings ELO
translations.py            Etiquetas y traducciones para la UI
form.py                    Logica auxiliar de forma manual
analyzer.py                Modulo experimental no integrado
test_betting_model.py      Suite principal de tests
render.yaml                Despliegue en Render
start-server.ps1           Arranque local en Windows
```

## Componentes clave

### 1. Motor de analisis

El nucleo del analisis esta repartido entre:

- [`betting_model.py`](./betting_model.py): probabilidad implicita, valor esperado, cuota minima, stake, confianza y seleccion de picks.
- [`app/engine.py`](./app/engine.py): orquesta el flujo principal de forecasting.
- [`app/forecasting.py`](./app/forecasting.py): execution score, ranking score y contexto operativo.
- [`app/calibrated_scoring.py`](./app/calibrated_scoring.py): adapta scoring y penalizaciones usando rendimiento historico.

### 2. Datos y proveedores

- [`app/providers.py`](./app/providers.py): conecta con `The Odds API`, `API-Football` y `SportsGameOdds`.
- [`app/sports.py`](./app/sports.py): resuelve el contexto del deporte, familias y mercados disponibles.
- [`elo.py`](./elo.py): aporta señal ELO cuando el contexto lo soporta.

### 3. Riesgo y disciplina operativa

- [`app/risk_controls.py`](./app/risk_controls.py): aplica filtros por perfil y calidad del pick.
- [`app/exposure.py`](./app/exposure.py): evita sobreexposicion por evento, liga o mercado.
- [`app/operating_mode.py`](./app/operating_mode.py): controla limites de picks, agresividad y diversificacion.

### 4. Tracking, aprendizaje y control de calidad

- [`tracking.py`](./tracking.py): guarda picks, apuestas reales, bankroll, resultados, `CLV` y metricas.
- [`app/calibration.py`](./app/calibration.py): mide calibracion real del modelo por segmentos.
- [`app/audit.py`](./app/audit.py): genera auditorias diarias de picks publicados.

### 5. Publicacion y narrativa

- [`app/telegram_service.py`](./app/telegram_service.py): mensajes, resumenes y botones para Telegram.
- [`app/ai_service.py`](./app/ai_service.py): narrativas y resumenes con OpenAI.
- [`app/ui.py`](./app/ui.py): capa visual de la UI HTML.

## Endpoints principales

### Salud y catalogo

```text
GET /status
GET /deportes-disponibles
GET /sportsgameodds/leagues
GET /sportsgameodds/eventos-debug
```

### Datos base

```text
GET /cuotas
GET /scores
GET /bankroll
POST /bankroll
POST /bankroll-form
```

### Analisis y picks

```text
GET /
GET /hoy
GET /informe-hoy
GET /pronosticos
GET /mejores-apuestas
GET /mis-apuestas
GET /dashboard
```

### Tracking y resultados

```text
GET  /tracking/picks
POST /tracking/apuestas
POST /tracking/registrar-apuesta-form
POST /tracking/picks/{pick_id}/importe
POST /tracking/picks/{pick_id}/cuota
POST /tracking/picks/{pick_id}/resultado
POST /tracking/liquidar-auto
GET  /tracking/stats
GET  /tracking/aprendizaje
GET  /tracking/riesgo
GET  /tracking/dashboard-data
GET  /tracking/evaluaciones
```

### Calibracion y auditoria

```text
GET  /api/calibration
GET  /api/calibration/report
GET  /api/audit
GET  /api/audit/report
POST /api/audit/send-telegram
GET  /api/audit/telegram
```

### Telegram

```text
GET /telegram/test
GET /telegram/test-botones
GET /telegram/enviar-pronosticos
GET /telegram/publicaciones
```

## Puesta en marcha

1. Crear entorno virtual:

```bash
python -m venv venv
```

2. Activarlo en Windows:

```powershell
venv\Scripts\activate
```

3. Instalar dependencias:

```bash
pip install -r requirements.txt
```

4. Copiar la configuracion:

```powershell
copy .env.example .env
```

5. Arrancar el servidor:

```bash
uvicorn main:app --reload
```

Tambien puedes usar:

```powershell
.\start-server.ps1
```

## Variables de entorno

Configuracion minima:

```env
APP_ENV=development
SHADOW_MODE=true
ODDS_PROVIDER=the_odds_api
ODDS_API_KEY=tu_api_key_de_the_odds_api
REFERENCE_BOOKMAKER=Pinnacle
```

Persistencia:

```env
BETTING_DB_PATH=betting_tracker.sqlite3
DATABASE_URL=
```

- En desarrollo local, si `DATABASE_URL` esta vacia, el proyecto usa `SQLite`.
- En despliegue cloud, si `DATABASE_URL` apunta a `PostgreSQL`, `tracking.py` cambia automaticamente a ese backend.
- `SHADOW_MODE=true` deja preparado el flujo para registrar publicaciones y auditoria sin enviar picks como si fuera entorno productivo.

Guard de publicacion en vivo:

```env
PUBLICATION_MIN_CLOSED_PICKS=80
PUBLICATION_MIN_CLV_SAMPLE=50
PUBLICATION_MIN_ROI=2.0
PUBLICATION_MIN_HIT_RATE=52.0
PUBLICATION_MIN_CLV_POSITIVE_PCT=52.0
PUBLICATION_MIN_MODEL_EVALS=60
```

- Si el sistema no supera esos minimos historicos, la publicacion live queda bloqueada y el flujo cae a modo sombra.

Configuracion opcional para Telegram:

```env
TELEGRAM_BOT_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id_o_id_del_canal
TELEGRAM_AUTOPUBLISH_ENABLED=true
TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS=6
TELEGRAM_AUTOPUBLISH_DEPORTE=todo
TELEGRAM_AUTOPUBLISH_PERFIL=alto_riesgo
TELEGRAM_AUTOPUBLISH_MODO=pinnacle
TELEGRAM_AUTOPUBLISH_MERCADOS=todo
TELEGRAM_AUTOPUBLISH_PARTIDO=todos
TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS=true
```

Configuracion opcional para OpenAI:

```env
OPENAI_ENABLED=true
OPENAI_API_KEY=tu_api_key_de_openai
OPENAI_MODEL=gpt-5
OPENAI_TIMEOUT_SECONDS=20
OPENAI_TELEGRAM_PICKS_MAX=3
```

Configuracion opcional de proveedores alternativos:

```env
SPORTSGAMEODDS_API_KEY=tu_api_key_de_sportsgameodds
SPORTSGAMEODDS_SPORT_ID=SOCCER
SPORTSGAMEODDS_LEAGUE_ID=MLS
SPORTSGAMEODDS_BOOKMAKERS=
SPORTSGAMEODDS_MAX_EVENTS=25

API_FOOTBALL_KEY=tu_api_key_de_api_football
API_FOOTBALL_LEAGUE=1
API_FOOTBALL_SEASON=2026
API_FOOTBALL_MAX_PAGES=1
```

La plantilla completa esta en [.env.example](./.env.example).

## Flujo recomendado

1. Revisar picks del dia en `/informe-hoy` o `/pronosticos`.
2. Guardar recomendaciones solo cuando quieras trackearlas.
3. Registrar aparte las apuestas realmente ejecutadas.
4. Liquidar resultados manual o automaticamente con scores.
5. Revisar `ROI`, `CLV`, aprendizaje, riesgo y calibracion.
6. Auditar periodicamente lo publicado para comprobar consistencia real.

## Modos de analisis

- `comparador`: usa `Pinnacle` como referencia y busca mejor precio en otras casas.
- `pinnacle`: analiza directamente la cuota de `Pinnacle`.

En general, `comparador` es el modo mas cercano a un flujo real de captura de precio.

## Perfil de riesgo

El sistema incluye perfiles de stake con filtros y exposicion diferentes:

- `conservador`
- `moderado`
- `agresivo`
- `alto_riesgo`

Esto permite separar una estrategia disciplinada de una mas especulativa.

## Base de datos y persistencia

Por defecto el proyecto trabaja con `SQLite` y deja trazabilidad de:

- Picks recomendados
- Apuestas reales ejecutadas
- Cambios de importe y cuota
- Resultados y liquidacion
- Publicaciones en Telegram
- Evaluaciones historicas
- Bankroll y settings

`tracking.py` tambien contiene compatibilidad para `PostgreSQL` en parte de la capa de persistencia.

## Despliegue

El proyecto incluye [`render.yaml`](./render.yaml) para desplegarlo como servicio web en Render.

Comando de arranque en produccion:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## Limitaciones actuales

- Sigue habiendo demasiada responsabilidad acumulada en [`main.py`](./main.py).
- La capa HTML continua acoplada a parte de la logica de presentacion y flujo.
- Hay soporte desigual por deporte y mercado segun proveedor.
- El repositorio tiene tests utiles, pero no cubre todas las rutas criticas.
- `analyzer.py` continua siendo experimental y no forma parte del flujo principal.

## Roadmap tecnico sugerido

### Fase 1

- Seguir sacando rutas, helpers HTML y orquestacion fuera de `main.py`.
- Alinear naming y contratos entre API, tracking y forecast engine.
- Reducir duplicacion entre funciones historicas y modulos nuevos de `app/`.

### Fase 2

- Separar claramente `routes`, `services`, `providers`, `repositories` y `templates`.
- Mover la persistencia de `tracking.py` a una capa mas aislada.
- Unificar respuestas con modelos Pydantic donde hoy aun salen dicts libres.

### Fase 3

- Mejorar observabilidad, logging estructurado y metricas operativas.
- Añadir mas tests de integracion para flujos de picks, liquidacion y auditoria.
- Reforzar calibracion y backtesting historico del modelo.

### Fase 4

- Multiusuario, autenticacion y configuracion persistente por cuenta.
- Panel de administracion y gestion de bankroll mas avanzada.
- Evolucionar hacia producto SaaS especializado si la validacion acompaña.

## Siguiente paso recomendado

La mejora tecnica mas rentable ahora mismo es dividir [`main.py`](./main.py) por dominios y mover la UI HTML a una capa mas separada del flujo de negocio.
