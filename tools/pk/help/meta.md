Metatile ids are global within a tileset pair (FRLG: primary 0x000-0x27F, secondary from 0x280).
pk meta ls MAP|PRIMARY,SECONDARY [--unnamed] -> id name behavior layer used:N solid:%
pk meta show MAP METATILE [--png out.png] -> 16x16 text drawing + colour legend
pk meta name MAP METATILE snake_name [--sym X] [--tags tag1,tag2] (first tag = terrain material)
pk meta autoname MAP [--overwrite] (names from behaviour/usage; never touches manual names)
Names: tools/pk/data/metatiles/<gTileset>.json. Edits accept names anywhere a METATILE is expected.
