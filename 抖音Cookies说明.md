# 抖音视频下载说明

## 问题
抖音视频现在需要 cookies 才能下载（yt-dlp 提示 "Fresh cookies are needed"）。

## 方案一：使用 Bilibili 替代（推荐）✅
我们的系统在 Bilibili 工作完美，不需要 cookies！
1. 找相同内容的 Bilibili 视频
2. 使用我们的系统处理即可

## 方案二：获取抖音 Cookies

### 步骤
1. 在 Chrome/Edge 中访问 [抖音](https://www.douyin.com) 并登录
2. 安装浏览器扩展：
   - Chrome: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - Edge: 在扩展商店搜索 "Get cookies.txt"
3. 访问抖音视频网页
4. 点击扩展图标 → 点击 "Export" → 下载 `cookies.txt`
5. 将 `cookies.txt` 放到项目目录 `d:\1\video2text\`
6. 再次运行转录

## 支持的平台
| 平台 | 状态 | 备注 |
|------|------|------|
| Bilibili | ✅ 完美 | 不需要 cookies |
| YouTube | ✅ 完美 | 不需要 cookies |
| TikTok | ⚠️ 需测试 | 可能需要 cookies |
| 抖音 | ⚠️ 需 cookies | 按上方说明获取 |
| 本地文件 | ✅ 完美 | 直接拖拽 |

## 使用示例
```bash
# Bilibili (推荐，无需 cookies)
python -m video2text --language zh transcribe "https://www.bilibili.com/video/BVxxxxxx"

# 抖音 (需 cookies.txt)
python -m video2text --language zh transcribe "https://v.douyin.com/xxxxxx"
```
