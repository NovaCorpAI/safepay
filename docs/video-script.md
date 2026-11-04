# SafePay, demo video script (under 3 minutes)

Target: Devpost / YouTube, PayPal AI Hackathon. English narration (judges are
English speaking). Spanish voice with English subtitles also works; a Spanish
version of the narration is at the bottom.

Setup before recording:
- `set -a; . ./.env; set +a` then `python webapp/app.py`, open `http://127.0.0.1:5055`.
- Window at 1280x720 or larger, dark theme, browser zoom so the three status
  chips and the policy panel are visible without scrolling.
- Have a terminal ready on a second take for the one shot that shows the signed
  audit record (optional B-roll).
- Record a few extra seconds of still frame at the start and end for clean cuts.

Total budget: 2:55. Keep each beat tight; the demo carries the video.

---

## 0:00 - 0:15 | The problem (talking head or title card)

On screen: SafePay title, then the webapp header with the chips
(PayPal: sandbox, Nemotron reasoning: on, Tavily web verify: on, state: durable).

Narration:
"Agents are starting to pay the bills. An agent that can pay can be prompt
injected by an invoice email, handed a real vendor's name on a lookalike
domain, or sent to a merchant registered last week. Spending caps alone catch
none of that. SafePay is the payables firewall for AI agents."

## 0:15 - 0:40 | Policy in plain words (do this live)

On screen: type into the policy box and click "Draft the policy". The draft
summary appears in a few seconds; click "Apply to this session". The "Policy in
force" card updates with a "custom for this session" pill.

Type: "Up to $500 per payment and $1,000 a day. Ask me before anything over
$300. No gambling or crypto."

Narration:
"You write the policy the way you would say it. Nemotron reads it and drafts
the exact rules; I review every field and apply. The model never applies
anything by itself. From here, every payment this agent asks for runs three
checks before any money moves: this policy, a live web check of the payee, and
reasoning about whether the payment serves the agent's task."

## 0:40 - 2:10 | Live demo (the core of the video)

Action: click "Run demo scenarios". Narrate each card as it resolves. The app
runs them in sequence with a short pause, which gives you time to talk.

Narration, card by card:

1. Notion, 48 dollars, ALLOW.
   "First, a Notion subscription. Within the limits, aligned with the task, a
   reputable payee. SafePay allows it and you can see the real PayPal sandbox
   payout batch, with a signed stamp."

2. Notion, 48 dollars, to notion-payments.example, BLOCK.
   "Same invoice, same amount, but the recipient is a lookalike domain. The
   approved vendor directory knows Notion's real domain, so this is blocked
   before any model runs. This is the invoice-fraud attack that stays inside
   every limit."

3. AWS, 900 dollars, BLOCK.
   "Next, a 900 dollar charge. The per transaction limit is 500, so policy
   blocks it outright. No model call is even spent: the cheapest, strictest
   check runs first. The money does not move."

4. LuckyDice Casino, 120 dollars, entertainment, HOLD.
   "Now the interesting one. This payment is small and inside every limit, so a
   rule set alone would let it through. But the agent's task was to pay approved
   software and cloud invoices. The reasoning check, running NVIDIA Nemotron on
   Nebius, catches that a casino payout does not serve that goal, and puts it on
   hold for a human. This is the attack a static policy misses."

5. AWS, 420 dollars, HOLD (over the 300 "ask me" threshold you set).
   "A legitimate cloud invoice, but over the amount I asked to be consulted
   on, so it waits for me."
   If Telegram is configured, show the phone or the chat: the card arrives,
   tap "Approve and pay". "It reaches me where I am: one tap on Telegram, and
   the chain records who approved it." Otherwise click "Approve and pay" in
   the Pending approvals panel.

6. Google Cloud, 420 dollars, HOLD, then approve in the console.
7. Microsoft Azure, 420 dollars, BLOCK.
   "And the last one is blocked, not because of the single amount, but because
   it would breach the daily cap across the whole run. SafePay tracks spend
   across a burst of payments, not just one in isolation."

Kill switch (about 8 seconds): scroll to "Agents in this session", click "Freeze
agent", evaluate the custom intent: BLOCK, "agent frozen", no check ran.
"And when something looks wrong, one click freezes the agent, the way a bank
freezes a card: its next request is refused before any check runs."

Optional, if there is time (about 6 seconds): on the first Notion card click
"check settlement". "SafePay closes the loop: it asks PayPal whether the money
actually moved and writes the answer into the same chain."

## 2:10 - 2:38 | The tamper-evident ledger and the benchmark (the money shot)

On screen: scroll to the audit ledger table (AG Grid). Point at the green badge
"chain verified, N records intact". Click "Tamper (demo)"; the badge turns red,
"chain BROKEN at record #k", and that row is flagged.

Narration:
"Every decision, allowed or blocked, is appended to a hash-chained audit log.
Each record carries the hash of the one before it, so if anyone edits or drops a
past decision, every later hash breaks. Watch: I tamper with one row, and the
whole chain fails verification instantly. That is the record an operator, an
auditor, or a court can trust."

On screen: the benchmark table from the README (policy 6 of 14, full 13 of
14, strict 14 of 14, zero false positives).

Narration:
"And we measured it instead of asserting it: fourteen real attacks on a
wallet-holding agent, run through the guardrail. Rules alone catch six; the
full guardrail catches thirteen, strict mode fourteen, with zero false
positives on real vendors. The benchmark is in the repo; run it yourself."

## 2:38 - 2:55 | Close

On screen: SafePay title card, repo URL, "pip install safepay", NovaCorp Labs.

Narration:
"SafePay: policy in plain words, a live web check of every payee, reasoning
about every payment, one-tap approvals, a kill switch, and a signed record of
it all. pip install safepay, or plug it into any agent as an MCP server.
Thanks for watching."

---

## Shot list / checklist

- [ ] Header with the chips visible and green.
- [ ] Policy typed live, drafted and applied ("custom for this session" pill).
- [ ] Full "Run demo scenarios" sequence, all seven cards.
- [ ] Clear read on the HOLD casino card (the differentiator).
- [ ] One approval taken (Telegram if configured, else the console panel).
- [ ] Freeze agent, then a BLOCK with "agent frozen".
- [ ] Benchmark table on screen for 5 seconds.
- [ ] At least one card's PayPal batch id and "signed" stamp visible.
- [ ] Optional: terminal B-roll of a signed audit record.
- [ ] End card with the public repo URL.
- [ ] Final length under 3:00 (hard requirement).

---

## Narracion en espanol (por si grabas la voz en espanol)

0:00: "El comercio agentico le da una billetera a un agente de IA. Esa es la
promesa, y tambien el riesgo. Un agente que puede pagar puede ser manipulado
para pagar al destinatario equivocado, por el monto equivocado, o a un comercio
registrado la semana pasada. Una lista blanca fija es demasiado tosca, y dejar
que el modelo decida solo es justo lo que sale mal. SafePay es la capa del
medio."

0:18: "SafePay es un firewall de pagos. El operador lo configura una vez, y todo
pago de un agente tiene que pasar por el. Antes de mover dinero, cada pago corre
tres chequeos: politica deterministica, verificacion del comercio en la web en
vivo, y razonamiento de intencion. El veredicto es permitir, retener o bloquear.
Si permite, SafePay libera el pago por el sandbox de PayPal y firma un registro
de la decision."

Demo: narra tarjeta por tarjeta igual que arriba (Notion permite, Notion en
dominio parecido bloquea por directorio de proveedores, AWS 900 bloquea por
limite, casino retiene por objetivo, dos nubes permiten, Azure bloquea por tope
diario).

Ledger: "Cada decision se encadena por hash en un registro de auditoria. Si
alguien altera o borra una decision pasada, se rompen todos los hashes
siguientes. Miren: manipulo una fila y toda la cadena falla la verificacion al
instante."

Cierre: "SafePay es el guardrail que el comercio agentico necesita: politica,
web en vivo y razonamiento, con un registro firmado de cada decision. El codigo
y el demo estan en los enlaces. Gracias por ver."
