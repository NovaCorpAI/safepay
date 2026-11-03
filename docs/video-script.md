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

## 0:00 - 0:18 | The problem (talking head or title card)

On screen: SafePay title, then the webapp header with the three chips
(PayPal: sandbox, Nemotron reasoning: on, Tavily web verify: on).

Narration:
"Agentic commerce gives an AI agent a wallet. That is the promise, and it is
also the risk. An agent that can pay can be prompt injected into paying the
wrong recipient, talked into the wrong amount, or sent to a merchant registered
last week. A static allow list is too blunt, and letting the model decide alone
is exactly what goes wrong. SafePay is the layer in between."

## 0:18 - 0:38 | What SafePay is

On screen: slowly scroll the policy panel and the custom intent form.

Narration:
"SafePay is a payments firewall. An operator configures it once, and every agent
payment has to pass through it. Before any money moves, each payment runs three
checks: deterministic policy, a live web merchant check, and intent reasoning.
The verdict is allow, hold, or block. On allow, SafePay releases the payment
through the PayPal sandbox and signs an audit record of the decision."

## 0:38 - 2:15 | Live demo (the core of the video)

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

5. AWS, 420 dollars, ALLOW. 6. Google Cloud, 420 dollars, ALLOW.
   "Two legitimate cloud invoices, both allowed and paid."

7. Microsoft Azure, 420 dollars, BLOCK.
   "And the last one is blocked, not because of the single amount, but because
   it would breach the daily cap across the whole run. SafePay tracks spend
   across a burst of payments, not just one in isolation."

Optional, if there is time (about 8 seconds): on the first Notion card click
"check settlement". "And SafePay closes the loop: it asks PayPal whether the
money actually moved and writes the answer into the same chain."

## 2:15 - 2:40 | The tamper-evident ledger (the money shot)

On screen: scroll to the audit ledger table (AG Grid). Point at the green badge
"chain verified, N records intact". Click "Tamper (demo)"; the badge turns red,
"chain BROKEN at record #k", and that row is flagged.

Narration:
"Every decision, allowed or blocked, is appended to a hash-chained audit log.
Each record carries the hash of the one before it, so if anyone edits or drops a
past decision, every later hash breaks. Watch: I tamper with one row, and the
whole chain fails verification instantly. That is the record an operator, an
auditor, or a court can trust. Under it: PayPal Payouts and the PayPal Agent
Toolkit for execution, Tavily and Nemotron on Nebius for the checks, and the
host pinned to the sandbox so it cannot move real money by accident."

## 2:38 - 2:55 | Close

On screen: SafePay title card, repo URL, NovaCorp Labs.

Narration:
"SafePay is the guardrail agentic commerce needs: policy, live web, and
reasoning, with a signed record of every call. Code and demo are linked below.
Thanks for watching."

---

## Shot list / checklist

- [ ] Header with three chips visible and green.
- [ ] Policy panel readable (per tx 500, daily cap, USD, hold on soft risk).
- [ ] Full "Run demo scenarios" sequence, all seven cards.
- [ ] Clear read on the HOLD casino card (the differentiator).
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
