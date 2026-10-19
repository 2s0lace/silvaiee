Experimental chunk layouts: diagnostic scoring and constrained overlap generation.

python tools/pk/pk.py layout learn
python tools/pk/pk.py layout score MAP FILE
python tools/pk/pk.py layout calibrate
python tools/pk/pk.py layout generate MAP --type route|town --exits N,S [--seed n] [--out .pk/FILE] [--keep FILE]
python tools/pk/pk.py layout check-paths FILE [--map MAP]

learn exports every lint.vanilla_outdoor map through blockout.export. No Pk*
map contributes. Two grids start at (0,0) and (2,2), with stride 4 on each axis;
only complete 4x4 chunks count. No padded cells become learning evidence.

Exact duplicate letter chunks are grouped. In descending observation-frequency
order (letter-content tie break), a chunk joins its nearest representative
within two changed cells, or becomes a new representative. Membership is bounded
by distance to the representative, never by a transitive similarity chain.
Representatives retain frequency, facing border letters, and every source map,
box and elevation. Directed adjacent-kind counts come only from neighbours on
the same grid, never from overlapping chunks on the other offset.

score excludes the target's entire vanilla layout, including aliases, before
merging representatives or learning adjacency. The fixed blockout exporter is
the existing classifier; held-out letters never train chunk/seam statistics.
Chunk fit is the fraction of chunks at Hamming distance <=2 from their nearest
representative. Seam fit counts each right/down neighbouring pair once: it
passes for an observed directed kind pair OR identical facing border letters.
The closest kind is used even for poorly fitting chunks; chunk fit must therefore
be read alongside seam fit. No neighbouring pair is reported as n/a, not 100%.
The worst five chunks include coordinates, distance and a representative's source.

calibrate holds out each vanilla map's entire layout independently, rebuilds the
vocabulary, and reports per-map p5/p50 (linear interpolation). It also scores
.pk/blockout/coast.txt against PkBlockCoast_Frlg and exports/scores all existing
PkV3*_Frlg maps in memory, read-only. Full per-map scores and worst chunks are in
.pk/chunks/calibration.json. OWNER DECISION: scores are diagnostic only, never
quality gates. Common empty lawn/open sea and schematic layouts can outscore
vanilla. The nearest-kind/source report identifies rare or odd chunks, rather
than ranking aesthetics. Calibration exits successfully even for weak separation.

Generation uses exact observed 4x4 letter/elevation variants, never merged
representatives or kind-pair adjacency. Source/placement stride is two cells on
both axes (including the cross phases absent from the two diagnostic grids).
Every two-column/two-row overlap agrees on letters AND elevation. Arc consistency,
frequency-weighted seeded choices and bounded backtracking solve the chunk WFC.
No invented rotations, edits, dropped fixed cells or relaxed overlap are offered.

Before filling, a minimum spanning tree connects exit segments, warp/door points
and optional --via x,y[;x,y...] spots. The existing paths.py directional A* routes
its edges, using its measured width median and straight-run p95 ceiling and bend
penalty. Width-two footprints become fixed p cells BEFORE chunk WFC. Known water,
trees, rock, buildings and all non-p --keep cells cannot be carved. A conflict
fails explicitly. Network cells, routes and waypoints are saved in FILE.json.

Requested sides get central, even-aligned two-cell walkable exit segments. Partial
domains must still permit all exits to connect at compatible elevations; a finished
branch must connect them. Forced water must remain connectable as one body and
have a water neighbour (no singletons). No pattern containing b is offered.
--keep uses the fixed blockout format plus ? for unknown letters. Non-? letters,
and their elevations if supplied, are hard constraints. Padding grows dimensions
to multiples of four, then crops; constraints are checked on the cropped map.

Anti-blandness is enforced INSIDE search, not by repairing an accepted layout.
Every layout.screens window has plain share <= vanilla stratum p95; mirror <=
vanilla stratum p95. Thresholds use the exact layout.measure and layout._p95
metatile calibration, holding out the target's entire layout. During generation,
the existing blockout.letter_measure adapter provides a conservative plain-cell
bound and a class mirror measure; after tiling actual layout.rows remains required.
Forced plain cells and an optimistic mirror bound prune partial branches; every
completed branch must satisfy both limits. Sources, exits, search counts and
limits are journaled beside the blockout as FILE.json. blockout.check follows.

Cache: .pk/chunks/corpus.json; exporter code/data/graphics stat fingerprints
invalidate it. Generator code uses the corpus version instead, so changing search
does not re-export unchanged vanilla. Cache hits, stale/invalid caches and exports print INFO.
learn also saves .pk/chunks/vocabulary.json with full provenance and adjacency.
All output/cache writes use the pk journal, support --dry-run and stay in .pk/.
No map data is written by layout generate. A contradiction or search-budget miss
fails explicitly without a blockout; partial results and fallback layouts are not saved.

Path/shore masks: learn also saves .pk/chunks/autotile.json, with native tileset
pair -> letter/other/8-bit-mask -> metatile counts. Clockwise bits start at N;
diagonals require both touching cardinal neighbours, giving the 47 blob shapes.
p uses other=*, s is measured next to lawn, and water next to sand or lawn.
The tiler derives same-pair plus compatible-primary tables with whole-layout
holdout; secondary IDs never cross pairs. Every context widening still intersects
the exact mask. An empty intersection offers the mask table alone with INFO;
a missing mask prints WARN reshape and refuses tiling, without invented tiles.
The final safety guard also rejects mask violations and new path-cell seams.
check-paths lists all missing shapes; --map applies the target layout holdout.
A diagonal-only one-cell path is impossible even if normalization yields a
standalone-cell mask. Such a path always prints the reshape warning.

Tests: python tools/pk/tests/test_chunks.py
