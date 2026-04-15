谷歌的symserver(https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll/)采用TimeDateStamp+SizeOfImage来索引chrome.dll, 缺少一个从版本的索引  
这个项目采用https://source.chromium.org/chromium/chromium/src/+/main:build/compute_build_timestamp.py来针对tag自动计算TimeDateStamp并拉取symserver的chrome.dll, 并且自动解析chrome.dll, 获取chrome.dll.pdb的链接并下载
