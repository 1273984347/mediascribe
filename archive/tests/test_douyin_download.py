"""
测试抖音下载（仅下载，不转录）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 提取链接
raw_input = "7.97 复制打开抖音，看看【无中生有头哥的作品】一人公司 | AI设计团队 头哥的AI硅基团队，完... https://v.douyin.com/0ZfD-oGOfow/ :0pm daA:/ 08/10 D@U.lP"
import re

url_match = re.search(r'https?://[^\s]+', raw_input)
if url_match:
    douyin_url = url_match.group(0).rstrip('/')
    print(f'🎯 链接: {douyin_url}')

    # 测试直接用 yt-dlp 下载
    import yt_dlp

    ydl_opts = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": "test_douyin.%(ext)s",
        "quiet": False,
    }

    # 尝试自动获取 cookies
    print()
    print('🍪 方案 1: 尝试自动从浏览器获取 cookies')
    try:
        ydl_opts["cookiesfrombrowser"] = ("chrome",)
    except Exception:
        pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(douyin_url, download=True)
            print('✅ 下载成功!')
            print(f'📋 标题: {info.get("title")}')
    except Exception as e:
        print(f'❌ 方案 1 失败: {e}')
        print()
        print('🍪 方案 2: 尝试使用 Edge 浏览器 cookies')
        try:
            ydl_opts["cookiesfrombrowser"] = ("edge",)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(douyin_url, download=True)
                print('✅ 下载成功!')
                print(f'📋 标题: {info.get("title")}')
        except Exception as e2:
            print(f'❌ 方案 2 失败: {e2}')
            print()
            print('🍪 方案 3: 无 cookies 尝试下载 (可能失败)')
            try:
                del ydl_opts["cookiesfrombrowser"]
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(douyin_url, download=True)
                    print('✅ 下载成功!')
                    print(f'📋 标题: {info.get("title")}')
            except Exception as e3:
                print(f'❌ 全部失败: {e3}')
                print()
                print('💡 解决方案:')
                print('  1) 在 Chrome/Edge 中访问抖音并登录')
                print('  2) 使用扩展导出 cookies.txt 到项目目录')
                print('  3) 或换用 Bilibili 视频')
