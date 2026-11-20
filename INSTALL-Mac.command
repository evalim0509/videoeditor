#!/bin/bash
# Double-click me to install autocut on this Mac.
cd "$(dirname "$0")"
echo "Installing autocut - this takes a while the first time (downloads tools and AI models)."
bash setup/mac.sh
echo
read -n 1 -s -r -p "Done - press any key to close this window."
