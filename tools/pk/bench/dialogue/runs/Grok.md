# Dialogue benchmark run: Grok

You are the "Grok" entry. Your output file is tools/pk/bench/dialogue/out/Grok.txt. Do it now, alone, start to finish.


NEW TASK, separate from the map task. Do not touch any map or game file. You write ONE text file:
tools/pk/bench/dialogue/out/Grok.txt

## Context
Pokémon FireRed/LeafGreen ROM hack. You write lines for 4 new NPCs and 1 sign in VIRIDIAN CITY, the town just
north of PALLET TOWN. Vanilla facts you may use:
- the GYM is locked, and its LEADER is away; nobody knows where;
- VIRIDIAN FOREST lies to the north on the way to PEWTER CITY;
- ROUTE 22 runs west towards the POKéMON LEAGUE gate;
- an old man in town teaches catching after his coffee.

Do NOT reveal who the GYM LEADER is.

## Style (FRLG register)
- English, short sentences, warm, a little humour. One idea per box.
- CAPS for POKéMON, items, places, TRAINER, GYM, ROUTE and names, exactly like vanilla: POKéMON, POKé BALL,
  PROF. OAK, ROUTE 22, VIRIDIAN CITY.
- No modern slang, memes, pop-culture quotes or meta jokes. The fourth wall breaks only on the TRAINER TIPS sign.
- At most 3 boxes per text. Input syntax: `||` = new box, `|` = forced new line; wrapping is automatic.
- Straight double quotes don't exist in the font; use “ ” if you need quotes. {PLAYER} is allowed.
- Policy: no item gifts, no quest requests, no promises of rewards.
- Night variants must feel different (mood, topic, behaviour), not the day line reworded.
- Viridian's voice: write your own one-line voice note first, and make every line fit it.

## The NPCs
1. old_timer: an elderly man standing near the locked GYM. Day and night text.
2. kid: a child near the POKé MART. Day text.
3. pond_lady: a woman sitting by the small pond in the town. Day and night text.
4. league_hiker: a hiker at the west exit towards ROUTE 22. Day text.
5. tips_sign: a TRAINER TIPS sign with one useful, true FRLG tip. Text.

## Output format (exactly this, one text per line)
```
voice: <your one-line voice note for Viridian>
[old_timer]
day: ...
night: ...
[kid]
day: ...
[pond_lady]
day: ...
night: ...
[league_hiker]
day: ...
[tips_sign]
text: ...
self: <your honest 0-10 rating of this dialogue against vanilla FRLG writing>
```

## Check, fix, finish
1. Run: python tools/pk/bench/dialogue/check.py Grok
2. Fix every FAIL line. WARN lines are style hints; fix them unless you have a good reason.
3. Re-run until the gate passes.
4. Reply "done", your self-rating, and the number of check runs you needed.
