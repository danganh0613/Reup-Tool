#!/bin/bash
# macOS: bấm đúp để đóng gói Edit Studio (Mac chip Apple) → release/mac/EditStudio-<ver>-macOS-arm64.dmg
cd "$(dirname "$0")" || exit 1
bash tools/build_mac.sh
echo
read -r -p "Nhấn Enter để đóng…" _
