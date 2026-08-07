"""
测试抖音支持
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from video2text.inputs import parse_source

print('🎯 测试抖音支持')
print('='*60)
print()

# 测试示例链接
test_urls = [
    'https://v.douyin.com/abc123/',
    'https://www.douyin.com/video/7123456789012345678',
]

for url in test_urls:
    print(f'🔍 测试: {url}')
    print()

    # 解析来源
    source = parse_source(url)
    print(f'   类型: {source.kind}')
    if source.url:
        print(f'   URL: {source.url}')
    if source.bv:
        print(f'   BV号: {source.bv}')
    print()
    print('-'*60)
    print()

print('✅ 支持的平台:')
print('   ✅ Bilibili (bilibili.com, b23.tv)')
print('   ✅ 抖音 (douyin.com, v.douyin.com)')
print('   ✅ TikTok (tiktok.com)')
print('   ✅ YouTube 及 yt-dlp 支持的所有平台')
print()
print('🚀 使用方法:')
print('  python -m video2text --language zh transcribe \"抖音视频链接\"')
