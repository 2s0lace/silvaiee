Overworld NPC sprites, cloned from a vanilla template (same frames, animations, shadow and palette).
pk sprite show GFX (size, frame, palette tag, allowed colours) | GFX = WOMAN_1 (FRLG first) or OBJ_EVENT_GFX_*
pk sprite export GFX name -> tools/pk/art/sprites/name.px (exact pixels, palette as pal line)
pk sprite build FILE.px name --like GFX
 Registers OBJ_EVENT_GFX_NAME (appended before NUM_OBJ_EVENT_GFX) next to the template in
 src/data/object_events/*.h and writes graphics/object_events/pics/people/name.png.
 Rebuilding only replaces the png. Size must equal the template sheet; one layer only.
 Colours must be from the template's palette (no new palettes): LIMIT lists the allowed ones.
 Pick a template whose palette fits the design (pk sprite show for several NPCs).
Use: pk ev add MAP obj @x,y gfx=NAME ... or blueprint npcs "<id> NAME @x,y ..."; then pk build.
