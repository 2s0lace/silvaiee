Shape: reusable natural silhouettes extracted from every vanilla outdoor map as material masks.
pk shape find --kind pond|cove|sandbar|beach|island|grove|tallgrass [--size WxH] [--limit 10] [--sheet]
Results are ranked by outline curviness. --size accepts exact matches first, then shapes within 2 tiles.
Each row is `ID SOURCE x,y WxH curvy:N`; --sheet writes original pixels beside each mask to .pk/render/shapes.png.
The index is cached at .pk/shapes/index.json and rebuilt when absent. Pk* maps are never source material.
pk shape place MAP KIND_OR_ID x,y [--flip h|v] [--dry-run]
A kind chooses its curviest result; an ID chooses exactly. The coordinate is the mask bounding-box origin.
Water masks are verified against game behavior: inaccurate water names/tags cannot turn land into a pond.
Ponds/coves copy every source water cell AND the complete one-cell bank ring, including metatile,
collision and elevation. Every copied tile must be a portable primary id, with the same primary
tileset and version as the destination. A secondary bank tile makes the shape NOT PLACEABLE;
there is no material-paint or bank-solving fallback. Find flags incomplete/non-portable bank rings.
Only the outer lawn ring is solved, using the lawn measured on the destination box perimeter.
Every water-outline cell must have a non-water, non-lawn bank neighbour; any lawn-facing side refuses
the placement. The complete copied water/bank patch must fit and remains frozen during cleanup.
Water-shape flips are refused because exact source-bank copying cannot mirror directional graphics.
Other material shapes retain h/v flips. Rotation remains unsupported.
Placement refuses event overlaps, new broken objects, or lost reachability, and rolls back pending writes.
The result includes material fidelity, seams, lint, and reach diagnostics. --dry-run skips shape cache/sheet writes.
