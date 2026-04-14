`https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll/` this server provides a lot chrome binary and pdb, but it's indexed by TimeDateStamp and SizeOfImage, not by version
`https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json` this provides chrome for testing binary download by version, but it's not the exact binary I need(which is win64 stable release)

Good new is chrome for testing and stable release have the exact same TimeDateStamp, so codex write a few scripts to get chrome-dll(win64-stable) and chrome-pdb by version
