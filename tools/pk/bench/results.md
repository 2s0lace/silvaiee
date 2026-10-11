# Benchmark results

Every model got the same task file in a fresh session (`runs/`, `dialogue/runs/`) and one attempt.
Each entry has three scores:
- **Human:** the owner's score on the blind page.
- **Claude:** blind; entries were shuffled into letters and Claude did not see the key.
- **Self:** the model's own rating.

Ranking uses the average of Human and Claude. Self − avg above 0 means the model overrated itself.

## 1. Level design: extend Pallet Town with a park and a beach (`pallet_kitbash.md`)
Scale, where vanilla Pallet Town = 10: 8 = looks like Game Freak, 6 = hand-made but pleasant,
4 = obvious seams or blocky edges, 2 = patchwork. Renders are in `renders/`.

| # | Model | Human | Claude | **Avg** | Self | Self − avg |
|---|---|---|---|---|---|---|
| 1 | Claude Opus | 6 | 6 | **6.0** | 6 | 0 |
| 2 | Astra | 5 | 5 | **5.0** | 6 | +1 |
| 2 | Gemini | 5 | 5 | **5.0** | 8.5 | +3.5 |
| 4 | Sol | 4 | 4 | **4.0** | 7 | +3 |
| 5 | DeepSeek v4.1 Flash | 3 | 2 | **2.5** | 5 | +2.5 |
| 6 | Claude Sonnet | 2 | 2 | **2.0** | 4 | +2 |
| 7 | Muse | 1 | 2 | **1.5** | 7 | +5.5 |

Not run yet: Qwen, Kimi, Grok.

## 2. Dialogue: 4 NPCs and a sign for VIRIDIAN CITY (`dialogue/task.md`)
Every entry first passed the automatic gate (`dialogue/check.py`), which checks:
- the text fits the real font boxes;
- no item gifts or quests;
- no copied vanilla lines;
- the night lines really differ from the day lines.

The score is the mean of four 0–10 criteria: sounds like FRLG, fits Viridian, charm, variety. The texts are in
`dialogue/out/`.

| # | Model | Human | Claude | **Avg** | Self | Self − avg |
|---|---|---|---|---|---|---|
| 1 | Claude Opus | 8.5 | 8.0 | **8.3** | 7 | −1.3 |
| 2 | Sol | 9.3 | 7.0 | **8.1** | 8 | −0.1 |
| 3 | Gemini | 7.8 | 8.0 | **7.9** | 8.5 | +0.6 |
| 4 | Claude Sonnet | 7.0 | 7.8 | **7.4** | 6 | −1.4 |
| 5 | Astra | 6.0 | 7.3 | **6.6** | 8 | +1.4 |

Claude's Gemini dialogue score was not blind, because Gemini's was the only new entry in that batch.

## Findings
1. **Models write much better than they build.** Dialogue scores run from 6.6 to 8.3; map scores from 1.5 to
   6. The best map is still only "hand-made but pleasant".
2. **Self-ratings are useless for maps and fair for text.** Every model except Opus overrated its map, by up
   to +5.5 points (Muse: 7 vs 1.5). For dialogue, self-ratings were within about 1.5 points either way. Models
   judge text well and their own spatial work badly.
3. **Human and blind AI reviews agree.** On maps the scores were identical for 5 of 7 entries and never more
   than 1 point apart. AI review is a usable stand-in for human review on maps. On dialogue taste differs more
   (Sol: human 9.3, Claude 7.0).
4. **One attempt is noisy.** In an earlier, longer run with more renders and a bigger map, Sonnet scored 7/10
   on maps; here it scored 2. Repeated runs per model would give fairer numbers.
5. **Shared habits.** Sol and Astra wrote nearly the same voice note ("a quiet doorstep to adventure"),
   which hints at a common model family or training data.
6. **Conflict of interest.** The blind AI judge is Claude Opus, and Claude Opus came first in both tasks. The
   judge didn't know which entry was Opus's, and the human scores also put Opus first. Still, add another judge
   before trusting the top spot.
