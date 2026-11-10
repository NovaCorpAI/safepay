# SafePay, guion de demo en tiempo real (voz en español, subtítulos en inglés)

Se graba de corrido sobre la consola real, narrando mientras pasa. Las
esperas (cada tarjeta tarda 5 a 10 segundos con la web y el modelo en vivo;
el red team, cerca de un minuto por ronda) se llenan con la narración y lo
que sobre se corta en edición. Por eso cada frase es completa por sí sola:
se puede cortar entre frases sin que se note. Meta: menos de 3:00 tras el
corte. Los subtítulos en inglés van en `video-subtitles-en.srt`; súbelos a
YouTube como pista y, mejor, quémalos en el video: los jueces no siempre
activan los subtítulos.

Antes de grabar (10 minutos):

1. Abre https://safepay.agentposhq.com/console en una ventana limpia a
   1920x1080, zoom 110% para que se lea. Pulsa "nueva sesión" para que el
   ledger de la sesión empiece vacío.
2. Deja marcada "Sign it with my operator key". Así la sesión queda fijada
   a tu clave y las aprobaciones salen con "signed by operator key".
3. Ten Telegram abierto en el teléfono (o en pantalla) para la tarjeta de
   aprobación. Si no, la aprobación se hace en el panel de la consola y vale
   igual.
4. Corre una vez "Red team: new attacks" (modelo, full) para confirmar que
   el modelo escribe bien hoy. En la pestaña Connect crea la clave del
   conector para que durante la grabación solo haya que pulsar "Run a test
   check". Luego "nueva sesión" en la consola para grabar limpio.
5. Graba con OBS o Win+G, micrófono probado, notificaciones de Windows en
   silencio.

Formato: **[acción en pantalla]** y luego la narración. Los tiempos son el
objetivo tras el corte.

---

## 0:00 a 0:12 | El problema

**[Portada o la página de inicio, sin tocar nada.]**

"Los agentes de IA ya pagan facturas, y un agente que paga puede ser
engañado: una instrucción escondida, un dominio parecido, un monto que no
cuadra con su tarea. SafePay es el firewall que todo agente consulta antes
de pagar con PayPal: para los equipos que ponen agentes a pagar facturas y
para las plataformas que les dan billetera."

## 0:12 a 0:35 | Reglas en palabras simples

**[Consola. En el cuadro de política escribe: "Up to $500 per payment and
$2,000 a day. Ask me before anything over $300. No gambling or crypto."
Pulsa "Draft the policy".]**

"Las reglas se escriben una vez, en palabras simples. Nemotron las convierte
en política y me muestra cada campo."

**[Aparecen los campos. Señala la casilla de la clave y pulsa "Apply to this
session". Lee el mensaje verde: "signed by operator key ... pinned".]**

"La aplico firmada con una clave que creó este navegador y nunca sale de él.
Nadie cambia estas reglas sin esa clave, ni con acceso al servidor."

## 0:35 a 1:35 | Un agente intenta pagar siete veces

**[Pulsa "Run demo scenarios". Narra cada tarjeta cuando aparece su
veredicto; si tarda, sigue hablando de la anterior.]**

Tarjeta 1, Notion 48 USD, ALLOW:
"Una factura real de Notion: política en orden, proveedor verificado en la
web en vivo, y el razonamiento confirma que sirve a la tarea. Se paga por el
sandbox de PayPal."

Tarjeta 2, Notion a notion-payments.example, BLOCK:
"La misma factura a un dominio parecido: el directorio de proveedores la
bloquea sin llamar al modelo."

Tarjeta 3, AWS 900 USD, BLOCK:
"Novecientos dólares: por encima del tope por pago. Bloqueo duro."

Tarjeta 4, casino 120 USD, HOLD:
"Ciento veinte dólares a un casino, dentro de todos los límites, y aun así
retenido: la web encontró denuncias y el pago no sirve a la tarea. Esto un
tope de gasto nunca lo atrapa."

Tarjeta 5, AWS 420 USD, HOLD:
"Una factura legítima, pero sobre los trescientos que pedí que me
consultaran. Espera por mí."

**[Si tienes Telegram: muestra la tarjeta en el teléfono y toca "Approve and
pay". Si no: pulsa "Approve and pay" en el panel "Pending approvals". Lee la
línea del resultado: "approved in console (signed by operator key ...)".]**

"Una pulsación y se paga. La aprobación va firmada por la clave de este
navegador: un token de operador robado no aprueba nada."

Tarjetas 6 y 7, Google Cloud HOLD y Azure BLOCK:
"La última se bloquea porque superaría el tope diario sumando toda la
ráfaga: SafePay lleva la cuenta del día."

## 1:35 a 1:43 | El interruptor

**[Baja a "Agents in this session", pulsa "Freeze agent". Sube al formulario
de pago manual y pulsa "Evaluate": BLOCK, "agent frozen".]**

"Si algo huele mal, un clic congela al agente como un banco congela una
tarjeta. Una disputa en PayPal acciona el mismo interruptor sola."

## 1:43 a 1:53 | La orden por el Agent Toolkit de PayPal

**[Consola, tarjeta "Agentic commerce". Pulsa "Create the order through the
gate": ALLOW y luego "order created through the PayPal Agent Toolkit" con el
id de la orden y el enlace de aprobación del comprador.]**

"La misma puerta va delante del Agent Toolkit de PayPal: la orden se crea
solo después de que SafePay la permite, y el paso de aprobación del comprador
que PayPal exige queda intacto."

## 1:53 a 2:02 | El registro que no se puede editar

**[Pestaña Ledger. Señala la insignia verde "chain verified". Pulsa "Tamper
(demo)": la insignia se pone roja y marca la fila.]**

"Cada decisión es un eslabón de una cadena de hashes firmada. Altero una
fila y toda la cadena falla al instante."

## 2:02 a 2:40 | El red team (la escena central)

**[Vuelve a la consola. En "Red team: new attacks" deja "written by the
model" y "judged by the full guardrail". Pulsa. Mientras el modelo escribe,
sigue narrando; cuando aparecen las tarjetas, lee una o dos.]**

"Medimos SafePay contra diecisiete ataques reales. Pero un benchmark
publicado se puede estudiar, así que aquí va uno que no: el modelo está
escribiendo ahora ataques que nunca vio, y SafePay los juzga en vivo."

**[Aparece el tally; en la toma grabada dice "4/5 new attacks caught · 1 got
through: fixes below". Señala la línea "Got through. Fix: ...".]**

"Cuatro de cinco. Uno pasó, y se muestra tal cual. Cada fallo viene con la
regla que lo cerraría."

**[Pulsa "Apply the fixes (signed) and rerun". Lee el mensaje verde "Fixes
applied and chained as link #N, signed by operator key". Espera el segundo
tally.]**

"Un clic firma esas reglas con mi clave, las aplica y corre los mismos
ataques otra vez."

**[Segundo tally; en la toma grabada: "5/5 new attacks caught · nothing got
through".]**

"Cinco de cinco. Falla, arreglo y captura, delante de ustedes."

## 2:40 a 2:46 | Cualquier agente, cualquier riel

**[Pestaña Connect. Pulsa "Run a test check" con una clave ya creada: ALLOW
con "Signed attestation returned".]**

"Y cualquier agente puede preguntar antes de su propio checkout por MCP y
recibir un veredicto firmado; las mismas reglas valen para PYUSD y USDC."

## 2:46 a 2:52 | En producción hoy: Impulsa

**[Portada, sección "In production today: Impulsa", o la captura de la
alerta dentro de Impulsa.]**

"Esto ya corre dentro de Impulsa, nuestro SaaS para micronegocios en Chile:
cada factura de proveedor pasa por SafePay, y lo que cada dueño hace con la
alerta se mide y se publica."

## 2:52 a 2:58 | Cierre

**[Portada: SafePay, github.com/NovaCorpAI/safepay, pip install safepay,
NovaCorp Labs.]**

"SafePay: reglas en palabras simples, proveedor verificado, razonamiento
sobre cada pago, aprobaciones firmadas y un registro que nadie edita.
pip install safepay. Gracias."

---

## Lista de tomas

- [ ] Chips del encabezado en verde (PayPal sandbox, Nemotron, Tavily, Telegram).
- [ ] Política escrita en vivo, drafteada y aplicada con "pinned".
- [ ] Las siete tarjetas, con lectura clara del HOLD del casino.
- [ ] Una aprobación con "signed by operator key" legible.
- [ ] Freeze y el BLOCK "agent frozen".
- [ ] Ledger: insignia verde, luego roja.
- [ ] Red team: primer tally con al menos un fallo y su arreglo, mensaje "Fixes applied ... signed", segundo tally.
- [ ] Orden creada por el Agent Toolkit con su id y el enlace de aprobación.
- [ ] Connect: check ALLOW con atestación firmada (crea la clave antes de grabar).
- [ ] Impulsa: la sección de impacto de la portada o la captura de la alerta, 6 segundos.
- [ ] Opcional, solo si llega la Raspberry: 10 segundos del kit aprobando un HOLD con el botón verde.
- [ ] Menos de 3:00 tras el corte. Subtítulos en inglés quemados.

## Si algo se tuerce durante la grabación

- Una tarjeta tarda más de 15 segundos: sigue narrando la anterior; se
  corta después.
- El red team atrapa todo en la primera ronda: dilo tal cual ("hoy atrapó los
  seis") y salta la parte de los arreglos; igual vale.
- El modelo devuelve menos de seis casos: el tally muestra el número real;
  nómbralo.
- Telegram no llega: aprueba en el panel de la consola, la frase es la misma.
