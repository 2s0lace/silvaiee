pk ev ls MAP -> obj#n warp#n coord#n bg#n with position, elevation, gfx, movement, script
pk ev add MAP obj @x,y gfx=LASS move=WANDER range=2x2 (text="..." | script=Label) [flag=FLAG_X] [id=LOCALID_X] [elev=n]
pk ev add MAP sign @x,y (text="..." | script=Label)
pk ev add MAP warp @x,y to=OTHER_MAP#n
pk ev add MAP trigger @x,y var=VAR_X=0 script=Label
text= appends Map_EventScript_Auto_N (msgbox ..., MSGBOX_NPC or MSGBOX_SIGN; end) and Map_Text_Auto_N.
gfx: short names; FRLG maps prefer *_FRLG graphics. move: WANDER, FACE_UP, LOOK_AROUND, ... or full MOVEMENT_TYPE_*.
pk ev set MAP obj#n @x,y move=... gfx=... range=WxH script=... flag=... text="..."
pk ev rm MAP obj#n|warp#n|coord#n|bg#n (a removed warp renumbers incoming warps; refuses if one targets it)
