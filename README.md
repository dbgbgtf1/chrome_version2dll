`https://chromium-browser-symsrv.commondatastorage.googleapis.com/?prefix=chrome.dll/`谷歌的symserver按照TimeDateStamp+SizeOfImage来索引提供很多旧版本chrome发行版二进制文件和pdb
`https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json`谷歌也按照版本索引提供旧版本的chrome-for-testing的二进制文件
但我们无法直接根据版本索引来获取旧版本chrome的发行版二进制文件和pdb, 好消息是chrome-for-testing的TimeDateStamp和发行版一模一样, 可以用来匹配. 所以我让codex给我写了一些脚本用来按照版本查询旧版本chrome发行版的二进制文件和pdb

[x] https://versionhistory.googleapis.com/v1/chrome/platforms/{platform}/channels/{channel}/versions?pageSize=1000从这里获取完整版本信息(然后存在cache_history_versions)
- 用完整版本信息来在cft_version_with_downloads.json里获取download_url, 然后下载
- 从chrome-for-testing里提取TimeDateStamp, 然后用来在symserver上索引获取到指定版本的正式发行版的chrome

注1: 仓库里的cft_version_with_downloads.json是作者此时从`https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json`拉取的, 如果需要更新版本支持, 可以手动去更新
注2: 这只是一个临时用ai搓的小工具, 提供思路参考, 只保证此时可以用, 不保证未来稳定可用
