`https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll/`谷歌的symserver按照TimeDateStamp+SizeOfImage来索引提供很多旧版本chrome发行版二进制文件和pdb  
`https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json`谷歌也按照版本索引提供旧版本的chrome-for-testing的二进制文件  
但我们无法直接根据版本索引来获取旧版本chrome的发行版二进制文件和pdb, 好消息是chrome-for-testing的TimeDateStamp和发行版一模一样, 可以用来匹配. 所以我让codex给我写了一些脚本用来按照版本查询旧版本chrome发行版的二进制文件和pdb

- `https://versionhistory.googleapis.com/v1/chrome/platforms/{platform}/channels/{channel}/versions?pageSize=1000`从这里获取精准版本信息, 存在cache_history_version
- 使用./get_history_versions.py, 运行后你应该能获取到更新的cache_history_version. 并且这个history只包含你指定的channel

- 用精准版本信息来在cft_version_with_downloads.json里获取download_url, 然后下载
- 使用./cft_download.py, 运行后你应该能在bianry下看到{version}-chrome-{platforms}.zip

- 从chrome-for-testing里提取TimeDateStamp, 然后用来在symserver上索引获取到指定版本的正式发行版的chrome.dll, range read判断出哪个是x86_64(或者你指定的架构)
- 使用./release_download.py, 运行后你应该能在bianry下看到{version}-{arch}-chrome.dll

- 从chrome.dll中提取GUID+AGE作为pdb的索引, 如果你不需要pdb, 可以略过这一步.
- 使用./pdb_download.py, 默认会用并行gzip-range模式下载服务端压缩流, 然后本地解压为pdb. 运行后你应该能在bianry下看到{version}-chrome.dll-pdb

注1: 仓库里的cft_version_with_downloads.json是作者此时从`https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json`拉取的, 如果需要更新版本支持, 可以手动去更新  
注2: 我只做了对chrome.dll和chrome.dll.pdb的拉取, 理论上可以拉取symserver支持的其他二进制文件和pdb. 同时我也只会完整测试stable win64是否可用  
注3: 这只是一个临时用ai搓的小工具, 提供思路参考, 只保证此时可以用, 不保证未来稳定可用
