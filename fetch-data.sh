#!/bin/bash
# Fetch the Chinese/Cantonese sources for import-cedict.py.
# All three are CC BY-SA; they are downloaded, not redistributed here.
#   CC-CEDICT  its maintainers, from CEDICT (c) 1997-98 Paul Andrew Denisowski
#   CC-Canto   (c) 2015-16 Pleco Software Incorporated
#   readings   (c) 2015-16 Pleco Software Incorporated
set -e
cd "$(dirname "$0")"
mkdir -p data && cd data
for u in https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.zip \
         https://cantonese.org/cccanto-170202.zip \
         https://cantonese.org/cccedict-canto-readings-150923.zip; do
  f=$(basename "$u")
  if [ -s "$f" ]; then echo "  have $f"; else
    echo "  fetching $f"; curl -fsSL -o "$f" "$u"
  fi
done
echo "ok -- now run ./import-cedict.py"
