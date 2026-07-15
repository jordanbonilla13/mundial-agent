# Mundial 2026 Betting Analyst

Proyecto en Python/FastAPI para analizar apuestas del Mundial 2026 usando cuotas reales, ELO y gestion conservadora de stake.

Importante: esto no garantiza beneficios. La mejora principal del proyecto no es "apostar mas", sino medir si existe ventaja real antes de aumentar la exposicion.

## Que hace ahora

- Descarga cuotas reales desde The Odds API.
- Usa The Odds API como proveedor principal de cuotas para el Mundial.
- Puede usar API-Football o SportsGameOdds como proveedores alternativos, pero sus planes gratuitos pueden no cubrir Mundial 2026.
- Usa Pinnacle como referencia principal.
- Compara otras casas contra Pinnacle para encontrar cuotas mejores donde apostar.
- Muestra el informe en espanol: equipos, resultado, perfil, modo y veredictos.
- Guarda snapshots de cuotas cada vez que se buscan recomendaciones.
- Calcula confianza por apuesta.
- Soporta mercado de goles `totals`: mas/menos goles cuando The Odds API lo devuelve.
- Soporta goles por equipo (`team_totals`) y corners por equipo (`alternate_team_totals_corners`) cuando The Odds API los devuelve.
- Usa ratings ELO desde World Football Elo Ratings.
- Modela mercado 1X2, incluyendo empate.
- Quita margen del mercado normalizando probabilidades implicitas.
- Mezcla mercado y ELO de forma conservadora.
- Calcula cuota minima aceptable, margen de cuota y valor esperado.
- Calcula stake con Kelly fraccional capado al 1.5% del bankroll.
- Permite senales `Value ELO especulativo` con micro-stake cuando el ELO contradice claramente al mercado.
- Guarda picks recomendados en SQLite para poder medir resultados.
- Liquida automaticamente picks con marcadores cuando el mercado se puede resolver con goles.
- Calcula ROI, hit rate y CLV medio.
- Muestra un dashboard por mercado, casa, perfil y tipo de modelo.

## Endpoints

```text
GET /
GET /cuotas
GET /scores
GET /mejores-apuestas
GET /hoy?bankroll=100
GET /hoy?bankroll=25&perfil=alto_riesgo&modo=comparador
GET /hoy?bankroll=25&perfil=agresivo
GET /hoy?bankroll=25&perfil=alto_riesgo
GET /hoy?bankroll=100&guardar=true
GET /informe-hoy
GET /informe-hoy?perfil=alto_riesgo&modo=comparador
GET /informe-hoy?perfil=agresivo
GET /informe-hoy?perfil=alto_riesgo
GET /pronosticos
GET /telegram/test
GET /telegram/enviar-pronosticos
GET /telegram/publicaciones
GET /tracking/picks
GET /tracking/picks?estado=pendiente
GET /tracking/stats
GET /tracking/aprendizaje
GET /tracking/dashboard-data
GET /dashboard
POST /tracking/liquidar-auto
POST /tracking/picks/{pick_id}/resultado
```

## Estructura

```text
main.py                  App FastAPI y endpoints
betting_model.py         Modelo 1X2, probabilidades, value y stake
tracking.py              Persistencia SQLite, resultados, ROI y CLV
elo.py                   Descarga y lectura de ratings ELO
form.py                  Forma manual por seleccion, aun sin integrar
analyzer.py              Modelo ponderado inicial, aun sin integrar
test_betting_model.py    Tests unitarios basicos
```

## Configuracion

Crea un archivo `.env` en la raiz:

```env
ODDS_PROVIDER=the_odds_api
ODDS_API_KEY=tu_api_key_de_the_odds_api
REFERENCE_BOOKMAKER=Pinnacle
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

# Alternativa opcional con SportsGameOdds
SPORTSGAMEODDS_API_KEY=tu_api_key_de_sportsgameodds
SPORTSGAMEODDS_SPORT_ID=SOCCER
SPORTSGAMEODDS_LEAGUE_ID=MLS
SPORTSGAMEODDS_BOOKMAKERS=
SPORTSGAMEODDS_MAX_EVENTS=25

# Alternativa opcional con API-Football
API_FOOTBALL_KEY=tu_api_key_de_api_football
API_FOOTBALL_LEAGUE=1
API_FOOTBALL_SEASON=2026
API_FOOTBALL_MAX_PAGES=1
```

The Odds API queda como proveedor principal aunque existan claves de otras APIs. Para forzarlo:

```env
ODDS_PROVIDER=the_odds_api
```

Para forzar API-Football:

```env
ODDS_PROVIDER=api_football
```

Para forzar SportsGameOdds:

```env
ODDS_PROVIDER=sportsgameodds
```

Endpoints utiles de SportsGameOdds:

```text
GET /sportsgameodds/leagues
GET /sportsgameodds/eventos-debug
```

En el free tier de SportsGameOdds puede ser obligatorio indicar `SPORTSGAMEODDS_LEAGUE_ID`. Si lo dejas vacio y tu plan no permite consultas globales de `SOCCER`, veras un error pidiendo leagueID o eventID.

Usa `/sportsgameodds/leagues` para ver las ligas disponibles en tu plan. En la prueba actual el free tier devuelve `MLS` y `UEFA_CHAMPIONS_LEAGUE`; si World Cup no aparece ahi, tu key gratuita no tiene acceso al Mundial en SportsGameOdds.

En API-Football, `API_FOOTBALL_LEAGUE=1` corresponde al Mundial/FIFA World Cup en la configuracion habitual de API-Football. Si la API no devuelve partidos o cuotas, revisa en su dashboard el ID de competicion disponible para tu plan.

Instala dependencias:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Arranca el servidor:

```bash
uvicorn main:app --reload
```

Abre:

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/informe-hoy
```

El informe incluye un formulario para cambiar bankroll, perfil y modo sin editar la URL manualmente.

Si activas Telegram:

- El servicio publica una tanda al arrancar.
- Despues vuelve a publicar cada `TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS` horas.
- Por defecto publica solo `stakazos`, pero puedes abrirlo a mas picks con `TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS=false`.
- Cada pick publicado queda guardado en tracking para luego marcarlo como ganada, perdida o nula, o liquidarlo automaticamente si el mercado se puede resolver por marcador.
- El historial de publicaciones se puede consultar en `GET /telegram/publicaciones`.

Modos disponibles:

```text
comparador  usa Pinnacle como referencia y busca mejores cuotas en otras casas
pinnacle    analiza solo Pinnacle directamente
```

El modo recomendado es `comparador`, porque suele ser mas realista buscar una cuota mejor que Pinnacle en otra casa que intentar ganarle a Pinnacle directamente.

Mercados disponibles en el informe:

```text
Todo
Resultado
Ambos equipos anotaran
Se clasificara
Doble oportunidad
Total de goles
Goles - intervalo
Corners
Tarjetas
Ambos equipos recibiran tarjetas
Equipo - mayor numero
Mas/menos goles por equipo
Mas/menos corners por equipo
Mas/menos faltas por equipo
Jugador - faltas concedidas
Jugador - recibira falta
Jugador - entradas
Jugador - remates a puerta de cabeza
Jugador - remates a puerta fuera del area
```

Los mercados de goles/corners por equipo dependen de que The Odds API los devuelva para ese partido/casa. Si no aparecen, no es un fallo del modelo: esa casa o evento no tiene ese mercado disponible en la API.

Faltas por equipo y varios mercados de jugador de tus capturas no estan disponibles como mercados oficiales en The Odds API para futbol. El selector los muestra, pero devuelve un aviso y no crea picks falsos.

## Flujo recomendado

1. Ver informe:

```text
GET /informe-hoy
```

2. Si quieres guardar picks recomendados:

```text
GET /hoy?bankroll=100&guardar=true
```

Tambien puedes registrar solo la apuesta que realmente metiste desde el informe HTML. Cada card recomendada tiene un campo `Importe real apostado` y un boton `Registrar que la aposte`.

3. Ver tus apuestas reales:

```text
GET /mis-apuestas
```

Desde ahi puedes:

- cambiar el importe real apostado;
- marcar `Ganada`, `Perdida` o `Nula`;
- ver beneficio, ROI y pendientes.

4. Ver picks pendientes por API:

```text
GET /tracking/picks?estado=pendiente
```

5. Cerrar un pick cuando sepas el resultado por API:

```http
POST /tracking/picks/1/resultado
Content-Type: application/json

{
  "resultado": "win",
  "closing_odds": 2.05
}
```

`resultado` acepta:

```text
win
loss
push
```

6. Revisar metricas:

```text
GET /tracking/stats
```

7. Liquidar automaticamente picks cuando The Odds API ya tenga marcador:

```http
POST /tracking/liquidar-auto
```

La liquidacion automatica funciona para mercados resolubles con marcador:

```text
1X2
Total de goles
Goles por equipo
Ambos equipos anotaran
Doble oportunidad
```

Corners, tarjetas, faltas y props de jugador no se cierran con este endpoint porque el marcador de The Odds API no trae esos datos de evento.

8. Abrir dashboard:

```text
GET /dashboard
```

9. Revisar aprendizaje basico:

```text
GET /tracking/aprendizaje
```

El aprendizaje actual no es una IA que se reentrene sola; guarda datos, resultados y cuotas para medir si el sistema tiene edge real con ROI y CLV. El dashboard separa el rendimiento por mercado, casa y perfil para detectar donde conviene subir o bajar riesgo.

## Modelo por mercado

El modelo ya no trata todos los mercados igual:

- 1X2 mezcla mercado y ELO.
- Total de goles usa una estimacion Poisson suave mezclada con la referencia de mercado.
- Goles por equipo usa Poisson por equipo con ajuste ELO.
- Corners y tarjetas usan una estimacion Poisson muy conservadora, porque hay menos informacion disponible.
- Ambos equipos anotaran usa probabilidad de gol de ambos equipos.

El mercado sigue mandando: estas estimaciones solo corrigen una parte pequena de la probabilidad para no fabricar value falso.

## Logica de stake

El stake se calcula con Kelly fraccional, pero con filtros:

- No apuesta si falta ELO fiable.
- No apuesta si la cuota es mayor que 7.
- No apuesta si el valor esperado es menor que 1%.
- No apuesta si el margen frente a la cuota minima es menor que 1%.
- Aunque haya value, el stake maximo por pick es 1.5% del bankroll.
- Si el ELO supera al mercado por al menos 6 puntos porcentuales y la cuota esta cerca de ser justa, puede marcar `Value ELO especulativo` con stake 0.25/5.

Perfiles disponibles:

```text
conservador  Kelly 15%, maximo 1.5% del bankroll, sin importe minimo
moderado     Kelly 30%, maximo 3% del bankroll, minimo practico 0.50 EUR si cabe en el limite
agresivo     Kelly 50%, maximo 8% del bankroll, minimo practico 1.00 EUR si cabe en el limite
alto_riesgo  Kelly 100%, maximo 50% del bankroll, importes objetivo de 5, 7.50 o 10 EUR
```

Los perfiles no solo cambian el importe: tambien cambian los filtros. Conservador exige mas margen y valor; alto_riesgo acepta senales mas especulativas.

Con bankroll pequeno, por ejemplo `25 EUR`, usa:

```text
GET /informe-hoy?perfil=agresivo
```

Ese perfil puede sugerir `1 EUR` en senales especulativas, que equivale a un 4% del bankroll. Es mucho mas arriesgado que el modo conservador.

Si quieres ver una ganancia/perdida mas notable con 25 EUR, existe:

```text
GET /informe-hoy?perfil=alto_riesgo
```

Ese modo puede sugerir:

```text
5.00 EUR   para Value ELO especulativo o Value ligero
7.50 EUR   para Value moderado
10.00 EUR  para Value interesante
```

Con bankroll de 25 EUR, una apuesta de 10 EUR es el 40% del bankroll. Es un modo de prueba agresiva, no una garantia de acierto.

Esto esta pensado para proteger bankroll mientras se recopila muestra.

## Cuando subir stake

No subir stake por intuicion. Subir solo si se cumplen varias condiciones:

- Muestra amplia de picks cerrados.
- ROI positivo.
- CLV medio positivo.
- Perdidas dentro de una varianza aceptable.
- El modelo sigue encontrando value despues de varios mercados reales.

## Tests

```bash
venv\Scripts\python.exe -m unittest -v
```

## Deploy en Render

El proyecto ya incluye un [render.yaml](/abs/path/C:/mundial-agent/render.yaml) basico para desplegar FastAPI como web service en Render.

Pasos:

1. Sube este repo a GitHub.
2. En Render, crea un `Blueprint` o un `Web Service` conectado al repo.
3. Si usas el blueprint, Render detectara `render.yaml`.
4. Si lo haces manual:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
```

5. En `Environment`, configura al menos:

```text
ODDS_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Variables recomendadas para mantener el comportamiento actual:

```text
ODDS_PROVIDER=the_odds_api
REFERENCE_BOOKMAKER=Pinnacle
TELEGRAM_AUTOPUBLISH_ENABLED=true
TELEGRAM_AUTOPUBLISH_INTERVAL_HOURS=6
TELEGRAM_AUTOPUBLISH_DEPORTE=todo
TELEGRAM_AUTOPUBLISH_PERFIL=alto_riesgo
TELEGRAM_AUTOPUBLISH_MODO=pinnacle
TELEGRAM_AUTOPUBLISH_MERCADOS=todo
TELEGRAM_AUTOPUBLISH_PARTIDO=todos
TELEGRAM_AUTOPUBLISH_SOLO_STAKAZOS=false
```

Importante sobre datos:

- Ahora mismo el tracking usa SQLite (`betting_tracker.sqlite3`).
- Si despliegas en Render sin disco persistente, los datos de picks y seguimiento pueden perderse en redeploys o reinicios.
- Para pruebas te puede servir asi.
- Para dejarlo serio, el siguiente paso recomendable es usar un `Persistent Disk` o migrar a `Postgres`.

## Siguientes mejoras

- Integrar forma reciente de selecciones.
- Guardar snapshots completos de cuotas para comparar closing line.
- Añadir filtros por fecha y competicion.
- Crear backtesting con historico real de cuotas.
- Separar el informe HTML en templates.
- Anadir dashboard local de rendimiento.

## Ultimo avance

En la ultima sesion se hizo el primer salto serio desde una app centrada solo en Mundial 2026 a una base de motor de apuestas mas general:

- Se desacoplo parcialmente la app del Mundial y se renombro la capa visual principal a `Betting Agent`.
- Se anadio un catalogo inicial de deportes en codigo: `worldcup`, `futbol`, `tenis` y `baloncesto`.
- Los endpoints principales (`/cuotas`, `/scores`, `/hoy`, `/informe-hoy`) ya aceptan `deporte`.
- Se introdujo un modo `market_only` mas conservador para deportes que aun no tienen una senal propia tipo ELO.
- Se anadieron `quality_score`, `elite_pick`, `elite_tier` y `source_strength` a las recomendaciones.
- Se anadio filtro `solo_elite` para quedarnos solo con picks premium.
- Se creo el endpoint `GET /pronosticos` como base para una futura salida tipo canal de Telegram.
- El tracking SQLite se amplio con soporte para `sport_key`, `sport_label`, `league_key`, `league_label`, `quality_score` y `elite_pick`.
- El dashboard se amplio para agrupar tambien por deporte, liga y tier de pick.
- Tras los cambios, los tests siguieron pasando:

```bash
venv\Scripts\python.exe -m unittest -v
```

## Siguiente paso recomendado

El siguiente paso para continuar manana es completar de verdad la fase multi-deporte, no solo la estructura:

1. Definir mercados permitidos por deporte.
2. Anadir logica especifica para `tenis` y `baloncesto`.
3. Evitar usar filtros pensados para futbol en deportes sin ELO o sin modelo propio.
4. Mejorar `/pronosticos` para generar un formato listo para Telegram.
5. Revisar la UI del informe para poder elegir deporte y modo elite desde formulario, no solo por query string.

Objetivo de la proxima sesion: convertir la arquitectura nueva en picks realmente utiles fuera del futbol del Mundial.

## Actualizacion de esta tanda

En la sesion siguiente se avanzo sobre ese punto y ya no solo existe la estructura, sino una primera separacion funcional por deporte:

- Se limitaron los mercados permitidos por deporte.
- `tenis` ahora trabaja como fase inicial solo con `h2h`.
- `baloncesto` ahora trabaja como fase inicial con `h2h` y `totals`.
- Se anadieron ajustes conservadores especificos para `tenis` y `baloncesto` en el modelo.
- El informe HTML ya incluye selector de `deporte` y check de `solo_elite`.
- `/pronosticos` ahora devuelve tambien `mensajes_telegram` mas utiles como base de publicacion.
- Se anadieron tests para alias de deporte, filtros por deporte y modelos base de `tenis` y `baloncesto`.

Estado validado al cerrar esta tanda:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `31` tests pasando.

## Proximo paso al retomar

1. Revisar visualmente `/informe-hoy` en navegador con `deporte=tenis` y `deporte=baloncesto`.
2. Afinar el texto y formato final de `/pronosticos` para dejarlo casi listo para Telegram.
3. Decidir si el siguiente deporte fuerte sera `tenis` o `baloncesto` y profundizar su modelo.
4. Empezar a separar la capa HTML del `main.py` si el informe sigue creciendo.

## Actualizacion mas reciente

Se profesionalizo un paso clave de la fase multi-deporte: el selector de deporte del informe ya no depende solo del catalogo fijo interno, sino que puede poblarse con deportes y ligas reales descubiertos desde el proveedor activo.

- Se anadio una capa `opciones_deporte_disponibles()` para construir opciones reales de seleccion a partir de `GET /deportes-disponibles`.
- El formulario de `/informe-hoy` ahora usa discovery real y mantiene la seleccion actual aunque sea una `sport_key` dinamica.
- Si el proveedor falla, no hay API key o discovery no responde, el sistema hace fallback automatico al catalogo interno para que la UI no se rompa.
- Por ahora el selector dinamico se limita a familias que ya tenemos trabajadas con una logica conservadora razonable: `soccer`, `tennis` y `basketball`.
- Se anadieron tests para cubrir seleccion dinamica persistente y fallback seguro del discovery.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `34` tests pasando.

## Siguiente paso recomendado

El siguiente paso con mas impacto real ya no es solo de interfaz, sino de calidad de picks:

1. Priorizar ligas por fiabilidad real y liquidez en vez de tratar todas igual.
2. Introducir un ranking interno de ligas y casas para penalizar mercados pequenos o poco estables.
3. Endurecer los criterios de `elite_pick` por deporte para que solo salgan picks verdaderamente recomendables para publicar.
4. Probar visualmente `/informe-hoy` con varias `sport_key` dinamicas reales del proveedor.

## Actualizacion actual

En esta tanda se ha dado un paso importante para que el agente no recomiende cualquier cosa solo porque el numero bruto salga bonito:

- Se anadio una capa de fiabilidad interna por liga, mercado y bookmaker.
- Las ligas top y mercados base como `h2h` y `totals` reciben mejor puntuacion de contexto.
- Las ligas menores, mercados mas volatiles y casas menos fiables quedan penalizados.
- La clasificacion `elite` y `stakazo` ahora exige no solo value y confianza, sino tambien un contexto de fiabilidad suficiente.
- Cada recomendacion ya sale con `reliability_score` y `reliability_tier` como base para mostrarlos luego en HTML, dashboard o mensajes de Telegram.
- Se anadieron tests especificos para comprobar que una liga top puede subir a `elite` y que una liga menor se quede en `seguimiento`.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `37` tests pasando.

## Siguiente paso recomendado

1. Mostrar `reliability_score` y `reliability_tier` en `/informe-hoy` y `/pronosticos`.
2. Ordenar picks no solo por `valor_esperado`, sino por una mezcla de `quality_score` y `reliability_score`.
3. Afinar el ranking de ligas con mas detalle segun pais, division y liquidez real.

## Ultimo remate hecho

Se completo justo la parte clave para buscar los mejores `stakazos`:

- `/hoy` ya ordena las recomendaciones por prioridad real del pick, no solo por `valor_esperado`.
- La prioridad ahora mezcla `elite_tier`, `quality_score`, `reliability_score`, confianza y margen.
- `/informe-hoy` ya muestra `Fiabilidad`, `reliability_score`, `quality_score` y el `tier` del pick dentro de cada tarjeta.
- `/pronosticos` y los mensajes tipo Telegram ya incluyen la fiabilidad del pick.
- El resumen de `/pronosticos` ahora informa tambien del numero de `stakazos`.
- Se anadieron tests para asegurar que un `stakazo` fiable sube por encima de picks menos robustos y que el mensaje Telegram ya lleva la fiabilidad.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `39` tests pasando.

## Siguiente paso recomendado

1. Llevar `reliability_score` y `quality_score` tambien al dashboard y al tracking.
2. Crear un filtro explicito de solo `stakazos` ademas de `solo_elite`.
3. Afinar el ranking interno de ligas top, medias y menores con una tabla mas detallada.

## Ultimo avance

Se completaron justo los dos puntos premium que faltaban para ir directos a los picks mas serios:

- Se anadio `solo_stakazos` al flujo principal de `/hoy`.
- `/informe-hoy` ahora permite filtrar no solo por `solo_elite`, sino tambien por `solo_stakazos`.
- `/pronosticos` acepta `solo_stakazos=true` para sacar solo la capa mas exigente del sistema.
- El dashboard ahora muestra `quality_media` y `reliability_media` por grupo.
- Se anadio un bloque premium en el dashboard para ver rendimiento agregado de los `stakazos`.
- Todo se resolvio sin romper compatibilidad con la base existente, aprovechando tambien `raw_json` cuando hace falta.
- Se anadieron tests para el filtro `solo_stakazos` y para las metricas de calidad/fiabilidad en dashboard.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `41` tests pasando.

## Siguiente paso recomendado

1. Llevar `reliability_score` y `quality_score` tambien a la vista de `mis apuestas`.
2. Anadir orden y filtros por `stakazo`, `elite`, `quality_score` y `reliability_score` en el tracking.
3. Refinar aun mas la tabla de ligas top/medias/bajas con criterios mas concretos.

## Ultimo avance

Se llevo ya el criterio premium a la vista de seguimiento real:

- `listar_picks()` ahora soporta filtros por `elite_tier`, `solo_elite`, `solo_stakazos`, `min_quality_score`, `min_reliability_score` y orden `premium`.
- `GET /tracking/picks` expone esos filtros tambien en JSON.
- `Mis apuestas` ahora incluye formulario de filtros por estado, tier, orden, quality minimo y reliability minimo.
- `Mis apuestas` ahora muestra `Tier`, `Quality` y `Reliability` tanto en pendientes como en cerradas.
- El orden `premium` prioriza `stakazo`, luego calidad, fiabilidad, confianza y value.
- Se anadieron tests para comprobar filtrado y orden premium en tracking.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `42` tests pasando.

## Siguiente paso recomendado

1. Afinar visualmente `Mis apuestas` con badges de color para `stakazo`, `elite` y `seguimiento`.
2. Anadir filtros por deporte y liga tambien en `Mis apuestas`.
3. Empezar a cruzar rendimiento real por tier con CLV y ROI para detectar si los `stakazos` lo estan siendo de verdad.

## Ultimo avance

Se remato la parte visual y de filtrado de `Mis apuestas`:

- `Mis apuestas` ya muestra badges de color por `stakazo`, `elite` y `seguimiento`.
- Se anadieron filtros por `deporte` y `liga` en esa pantalla.
- `GET /tracking/picks` ahora tambien acepta `sport_label` y `league_label`.
- `listar_picks()` ya puede filtrar por deporte y liga usando los datos guardados en `raw_json` o en columnas directas.
- Se anadio test para validar el filtrado por deporte y liga en tracking.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `43` tests pasando.

## Siguiente paso recomendado

1. Cruzar `stakazo/elite/seguimiento` con CLV, ROI y hit rate de forma mas visible.
2. Anadir un resumen arriba de `Mis apuestas` con cuantos `stakazos` tienes pendientes y cerrados.
3. Refinar aun mas el criterio de ranking por liga para que el filtro premium sea cada vez mas selectivo.

## Ultimo avance

Se hizo el cruce visible entre tiers premium y rendimiento real:

- El dashboard ahora calcula `clv_medio`, `clv_muestras` y `clv_positivo_pct` por grupo.
- Se anadieron bloques comparativos entre `stakazos`, `elite` y `seguimiento`.
- Ahora ya se puede ver de forma mucho mas clara si los `stakazos` baten de verdad en `ROI`, `hit rate` y `CLV`.
- Las tablas del dashboard muestran tambien `CLV medio` y `% de CLV positivo` por grupo.
- Se anadio test especifico para validar el calculo de `CLV` por tier.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `44` tests pasando.

## Siguiente paso recomendado

1. Anadir un resumen premium en `Mis apuestas` con `stakazos` pendientes, cerrados, ROI y CLV.
2. Empezar a penalizar aun mas los tiers cuyo `CLV` o `ROI` real no acompane.
3. Crear un pequeno ranking interno de ligas basado ya no solo en heuristica, sino tambien en rendimiento historico real.

## Ultimo avance

Se llevo tambien el resumen premium a `Mis apuestas`:

- `Mis apuestas` ahora muestra un bloque superior con `stakazos` pendientes, `stakazos` cerrados, `ROI stakazos` y `CLV stakazos`.
- En ese mismo bloque se comparan tambien `elite` y `seguimiento` a nivel de cerradas y `ROI`.
- La vista usa la misma fuente de metricas que el dashboard, asi que no hay duplicidad de logica.
- Se reforzo el test de `CLV` por tier para garantizar que el resumen premium se apoya en cifras consistentes.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `44` tests pasando.

## Siguiente paso recomendado

1. Empezar a penalizar automaticamente tiers o ligas cuyo `CLV` y `ROI` real empeoren con muestra suficiente.
2. Construir un ranking de ligas apoyado en rendimiento historico real.
3. Anadir alertas visuales cuando un tier premium pierda calidad real en tracking.

## Ultimo avance

Se ha dado el paso mas importante de aprendizaje real hasta ahora:

- Se anadio `penalizaciones_historicas()` para detectar ligas y tiers con mal comportamiento real cuando ya hay muestra suficiente.
- La penalizacion se basa en `ROI`, `CLV medio`, `% de CLV positivo` y `hit rate`.
- `/hoy` ya aplica esa penalizacion automaticamente a las recomendaciones nuevas.
- Si una liga o un tier premium va claramente mal en historico, el sistema reduce `quality_score`, `reliability_score` y puede degradar el pick de `stakazo` a `elite` o incluso a `seguimiento`.
- El informe HTML ya muestra el `Ajuste historico` aplicado al pick.
- Se anadieron tests para validar tanto la deteccion de penalizaciones historicas como el downgrade automatico dentro de `/hoy`.

Estado validado tras este avance:

```bash
venv\Scripts\python.exe -m unittest -v
```

Resultado: `46` tests pasando.

## Siguiente paso recomendado

1. Convertir esa penalizacion historica en un ranking persistente de ligas.
2. Hacer que el peso de la penalizacion dependa tambien del deporte y del mercado.
3. Anadir alertas mas visibles cuando una liga premium entre en zona roja por CLV o ROI.

## Siguiente paso al retomar

El siguiente paso prioritario al volver es convertir la penalizacion historica en un ranking persistente de ligas, para que el sistema aprenda de forma acumulativa donde merece buscar `stakazos` y donde no.

Objetivos concretos de la siguiente tanda:

1. Crear un ranking interno de ligas basado en `ROI`, `CLV medio`, `% de CLV positivo` y muestra cerrada.
2. Hacer que ese ranking afecte al peso de la recomendacion antes incluso de clasificar `elite` o `stakazo`.
3. Ajustar la severidad de la penalizacion segun `deporte` y `mercado`.

## Actualizacion de Telegram automatizado

Se ha dejado lista la primera automatizacion real del canal:

- El servicio puede publicar una tanda automaticamente al arrancar.
- Despues vuelve a publicar cada 6 horas, configurable por `.env`.
- La publicacion automatica usa el mismo motor de `/pronosticos`.
- Cada pick publicado se guarda en SQLite y queda enlazado a su publicacion de Telegram.
- Se anadio `GET /telegram/publicaciones` para revisar que se publico y si esos picks siguen pendientes o ya salieron ganados, perdidos o nulos.
- Como los picks publicados quedan en tracking, tambien se pueden cerrar manualmente o con `POST /tracking/liquidar-auto` cuando el mercado lo permita.




ULTIMO CHAT HASTA QUEDARME SIN TOKENS: vale, ya le ponmgo todo y ya me da bets de baloncesto futbol y tal, y para conectar esto a telegram?






2:39 AM






Worked for 1m 7s



Ya te lo he dejado preparado para conectar el proyecto a Telegram de forma simple y usable.
He añadido en [main.py (line 429)](/abs/path/C:/mundial-agent/main.py:429) la capa real de envío a Telegram, y dos endpoints nuevos:
/telegram/test para comprobar que el bot y el chat están bien configurados
/telegram/enviar-pronosticos para publicar en Telegram el resumen y los picks premium que ya genera /pronosticos
También añadí las variables a [.env.example (line 4)](/abs/path/C:/mundial-agent/.env.example:4):
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
Para usarlo:
Crea el bot con @BotFather y copia el token.
Mete ese token en .env como TELEGRAM_BOT_TOKEN=...
Saca tu chat_id o el ID del canal/grupo y ponlo como TELEGRAM_CHAT_ID=...
Reinicia el servidor.
Prueba primero:
/telegram/test
Si responde bien, publica picks con algo como:
/telegram/enviar-pronosticos?deporte=todo&modo=comparador&solo_stakazos=true
La suite quedó bien: 56 tests OK.
Si quieres, el siguiente paso te lo hago ya más profesional todavía: un modo automático para que publique solo los mejores picks a una hora concreta o cuando detecte nuevos stakazos.

Edited 3 files+151-0Review changesUndoReview

.env.example.env.example+2-0
main.pymain.py+102-0
test_betting_model.pytest_betting_model.py+47-0





2:40 AM






guarda todo ewsto en readme qu7e me he quedado sin tokenas






2:41 AM







You've hit your usage limit. Review your workspace's usage settings to continue.
