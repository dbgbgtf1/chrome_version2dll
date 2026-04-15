谷歌的symserver(https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll/)采用TimeDateStamp+SizeOfImage来索引chrome.dll, 缺少一个从版本的索引  
这个项目采用https://source.chromium.org/chromium/chromium/src/+/main:build/compute_build_timestamp.py来针对tag自动计算TimeDateStamp并拉取symserver的chrome.dll, 并且自动解析chrome.dll, 获取chrome.dll.pdb的链接并下载

Quick Start
1. dget_history_versions.py会帮你从https://versionhistory.googleapis.com/v1/chrome/platforms/{platforms}/channels/{channel}/versions抓取你的目标channel和platform, 把历史记录保存到cache_{channel}\_history_versions
2. dll_download.py会按照你给的大版本范围和channel从cache_{channel}\_history_versions获取具体版本信息, compute_build_timestamp.py来计算timedatestamp, 随后算出symserver上的对应dll上链接, 并请求用户确认下载到binary\dll下
3. pdb_download.py会根据binary\dll下的dll解析得出symserver上对应版本dll的pdb, 并请求用户确认下载到binary\pdb下
