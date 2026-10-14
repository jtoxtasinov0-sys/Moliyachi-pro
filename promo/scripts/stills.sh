#!/usr/bin/env bash
# usage: scripts/stills.sh name t1 t2 ...  -> renders/previews/stills_<name>.jpg (0.5 scale)
name=$1; shift; rm -rf cache/st; mkdir -p cache/st; i=0
for t in "$@"; do node src/render.js --from $t --to $(python3 -c "print($t+0.01)") --fps 100 --scale 0.5 --out cache/st/$i --jpg >/dev/null && mv cache/st/$i/00000.jpg cache/st/$(printf %02d $i).jpg; args+=(-label "${t}s" cache/st/$(printf %02d $i).jpg); i=$((i+1)); done
montage "${args[@]}" -tile ${COLS:-4}x -geometry +6+6 -background '#202020' -fill white -pointsize 22 renders/previews/stills_$name.jpg && echo renders/previews/stills_$name.jpg
