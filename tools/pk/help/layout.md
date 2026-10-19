Experimental chunk layouts: Parts A/B. Generation is gated on calibration.

python tools/pk/pk.py layout learn
python tools/pk/pk.py layout score MAP FILE
python tools/pk/pk.py layout calibrate

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
.pk/chunks/calibration.json. The Part B gate requires vanilla p50 seam fit to
be clearly higher than the coast's. Both the point-estimate margin and the
distribution-free 95% order-statistic interval for the vanilla median are
printed. A positive point-estimate gap alone is insufficient: its interval's
lower bound must exceed the coast. This supplies evidence for "clearly higher"
without inventing a fixed fit threshold or required percentage-point margin.
Insufficient maps or overlapping uncertainty explicitly stop before Parts C/D.

Cache: .pk/chunks/corpus.json; source-code/data/graphics stat fingerprints
invalidate it. Cache hits, stale/invalid caches, and re-exports print INFO.
learn also saves .pk/chunks/vocabulary.json with full provenance and adjacency.
All output/cache writes use the pk journal, support --dry-run and stay in .pk/.
No map data is written. Generation, --keep, tiling and renders are pending the
calibration gate; unsupported commands fail explicitly.

Tests: python tools/pk/tests/test_chunks.py
