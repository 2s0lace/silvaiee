# <Your hack's name>: Creative Vision

<!--
HOW TO USE THIS TEMPLATE
Copy it to blueprints/VISION.md in your pokeemerald-expansion repo and fill in every <...>.
Agents read this file once per session (AGENT.md points here); it settles every design decision.
Write short, concrete rules. Agents follow what is written, not what you meant.
Sections marked "default" hold rules that worked for us. Keep them, or change them deliberately.
-->

Base: pokeemerald-expansion <version>, FRLG build.
Audience: the LLMs that write and apply blueprints (`tools/pk/SPEC.md` §14).

> ## Content policy
> - **Allowed:** <e.g. new flavour NPCs, new trainers>
> - **Postponed:** <e.g. quests until the maps are built>
> - **Not allowed:** <e.g. new items>
> - Enforced by `tools/pk/data/policy.json`; keep the two in sync.
> - Agent roles: `tools/pk/agents/` <list who does what>.

> ## Scope
> - **Rework:** <which areas get changed, e.g. plain overworld towns and routes>
> - **Focus:** <areas that deserve extra love>
> - **Keep as is:** <iconic or already-good areas; agents touch them only for a concrete reason>

## 1. The one-sentence pitch
<What should a player feel that vanilla doesn't give them?>

## 2. Pillars (in priority order)
<!-- 3-5 principles. When two of them conflict, the higher one wins. Examples: respect the original,
place over size, a living region, terrain with character, dialogue that builds the world. -->
1. <pillar>
2. <pillar>
3. <pillar>

## 3. Engine features to consider (owner decides)
| Feature | Config | Use |
|---|---|---|
| <e.g. follower Pokémon> | <config flag> | <why> |

## 4. Area identities
<!-- One row per town or area: what makes it unlike the others. Agents use this for terrain and dialogue. -->
| Area | Identity | Terrain language | New district or addition |
|---|---|---|---|
| <town> | <one phrase> | <materials, shapes> | <what gets added> |

## 5. Map design rules (defaults)
- Size: towns ×1.3–1.6 area, routes +6–12 tiles on the narrow axis, always within
  `(w+15)*(h+14) ≤ 10240`.
- The vanilla core is a `keep` rect.
- Prefer growing right and bottom: then vanilla coordinates, script literals and connection offsets stay valid.
- Never create a bypass. Each new area attaches to one progression zone; list its blockers under `gates`.
- New buildings only with an interior that reuses a vanilla interior layout; otherwise use decorative structures.
- Never place anything that blocks the only path or a vanilla trigger.
- <your own rules>

## 6. Writing style (defaults)
- <language>, FRLG voice: short, warm, slightly humorous. CAPS for POKéMON, items and places, as in vanilla.
- At most 3 text boxes per NPC. No modern slang and no meta jokes, except in TRAINER TIPS.
- Each town gets a one-line voice note before any dialogue is written.

## 7. Art direction
<!-- e.g. "tastefully richer than vanilla": a bit more detail on objects, calm ground, strict FRLG outlines and
light. The asset critic reads its thresholds from here once it exists. -->
<your art direction>

## 8. Parked ideas
<!-- Ideas for later. Agents may foreshadow them in dialogue but must not implement them. -->
- <idea>

## 9. Rollout order
1. <first area or demo slice, e.g. starting town → first route → second town>
2. <...>
