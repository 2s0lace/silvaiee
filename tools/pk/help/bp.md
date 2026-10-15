pk bp world [filter] [--status plan|draft|applied] | pk bp show MAP [--section npcs]
pk bp plan MAP (dry run: ops, warnings) | pk bp check MAP [--strict] (plan + map check + gates + traps)
 Errors: GATE (bypass), TRAP (reachable area with no way back), duplicate ids, structure on water/keep/
 another structure/under an NPC. --strict also fails on unseen seams (use it before handing off).
 Later terrain lines win: a brush after a forest carves it.
pk bp render MAP [--scale 2] (.pk/render/MAP.bp.png, applied in memory only) | pk bp diff MAP
pk bp apply MAP (ONE transaction; only with the owner's OK) | pk bp export MAP [--out f.bp]
Blueprint files: blueprints/<Region>/<Map>.bp. Format: tools/pk/SPEC.md section 14.
size WxH grow=right:8,bottom:4 [fill=extend|border|terrain] (absolute target; re-apply is a no-op)
terrain: keep x,y WxH | fill MAT | rect x,y WxH MAT | circle x,y rN MAT | path x,y x,y ... width=n MAT
 | river x,y x,y ... width=n | ledge x1,y1 x2,y2 | MAT=forest uses the "tree" stamp. h=/ramp: not modelled.
 Words: grass = plain grass ground, tall_grass = encounter grass, rock = solid; tagged metatiles add more.
structures: id STAMP @x,y (top-left; pk stamp ls; buildings from pk stamp from-tileset; add the door warp under warps)
raw x,y (header; or "at x,y" inside) then "def X 0x2A3 [coll=n elev=n]" and rows; ? or space keeps; named --sym metatiles need no def
npcs: id GFX @x,y MOVE [range=WxH] (text="..." | script=Label) [text_night="..."] [when=night|dark|day|...]
trainers: id GFX @x,y MOVE sight=n trainer=TRAINER_X intro=".." defeat=".." after=".." (party by GLM)
signs: id @x,y text="..." | triggers: id @x,y var=VAR_X=0 script=Label | warps: id @x,y -> Map#n
vanilla: keep all | move REF @x,y | set REF move=.. gfx=.. range=.. | remove REF | text Label: "..."
texts: Label: "plain text" | connections: up MAP_X offset=n|auto | header music=.. weather=.. type=..
Policy (tools/pk/data/policy.json): items and declare var are rejected; quests postponed.
Ids are stable: NPCs get LOCALID_BP_<MAP>_<ID>, scripts <Map>_EventScript_BP_<id>; removed ids are pruned.

Terrain kitbash steps:
  copy SRC_MAP x,y,WxH @tx,ty
  auto x,y,WxH
All material brushes run first; copy/auto then run in file order. Copy always repairs the seam.
Check validates source maps, both bounds and portability through the map copy implementation.
