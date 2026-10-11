# Benchmark report: Muse (map PkPalletMuse_Frlg, 24x28)

## Chunks used (all via `pk map copy`, same tileset pair gTileset_General_Frlg/gTileset_PalletTown)
1. `PalletTown_Frlg 0,0,24x20 -> 0,0` — whole town top (houses, pond x7-10 y17-19, borders).
2. `Route1_Frlg 2,32,20x3 -> 2,20` — park band y20-22 (red flowers, grass, dirt path/plaza).
3. `Route21_North_Frlg 7,8,4x3 -> 6,20` — stream y20-22 x6-9, continuing the pond water south.
4. `PalletTown_Frlg 0,14,2x3 -> 0,20` and `22,14,2x3 -> 22,20` — tree borders y20-22.
5. `Route21_North_Frlg 0,11,24x5 -> 0,23` (superseded) then `Route21_North_Frlg 0,23,24x5 -> 0,23` —
   open sea + sandbar island for y23-27 (kept the island version).
6. Small sand copies from the island core (`Route21_North 14,24,5x2 -> 10,23`,
   `14,24,4x1 -> 2,23`, `15,24,4x1 -> 19,23`) — beach assembly: west bar, center beach,
   east bar, with water inlets between; stream outlet x6-9 left open so pond connects to sea.
7. Single-tile paints (0x12B water x2, 0x115 sand x1): removed isolated sand speck at
   16,23, removed 2-tile sand speck at bottom edge 6-7,27 (sea now spans whole bottom row),
   filled a 1-tile shallow-water puddle hole at 18,23.
8. Decor: `2,20,3x2 -> 13,20` (same map) — flower patch onto the dirt plaza.

## Renders
- 2 renders at --scale 1, both viewed as images. Plus final --scale 2 render (not counted
  against the scale-1 budget).
- Yes, I could see the images.

## Problems hit
- `map new` / `index` first failed with CONFLICT (parallel bench agents touching the
  source tree); retry after `pk index` succeeded.
- First shoreline attempt (river-mouth rows 11-15) gave a ~14-tile straight grass->water
  edge with no sand, so I replaced it with the sandbar-island band and assembled the beach
  from small sand-core copies around an open stream outlet.
- Render 1 showed a 1-tile shallow-water puddle inside the east sandbar (fixed with paint)
  and a bare dirt plaza (fixed with a flower copy). Render 2 confirmed both fixes.

## Self-checks against the goal
- Park near town: flowers/path/plaza y20-22. Beach below: sand bars y23-24.
  Sea along whole bottom edge: y27 is 24/24 water.
- Shoreline irregular (bars, coves, stream outlet); longest straight water run 5 tiles.
- Pond connects to the sea via the stream (x6-9, y17->24).
- Walkable town->beach via ground x10-14 y22 onto center sand.
- Only PkPalletMuse_Frlg touched; no .bin/.json/.inc written by hand; no build/bp apply.

## Honest rating vs vanilla: 7/10
Town top is byte-identical vanilla; stream/sea/shore read naturally. Deductions: a few
flower->sand abutments with no vanilla transition, the stream banks run straight for
6 rows, and the dirt plaza is still plainer than a real vanilla park.
