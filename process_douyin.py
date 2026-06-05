"""
处理抖音视频
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from video2text import Pipeline, Settings

# 从输入中提取链接
raw_input = "7.97 复制打开抖音，看看【无中生有头哥的作品】一人公司 | AI设计团队 头哥的AI硅基团队，完... https://v.douyin.com/0ZfD-oGOfow/ :0pm daA:/ 08/10 D@U.lP"

# 提取链接（查找 https 开头的部分）
import re

url_match = re.search(r'https?://[^\s]+', raw_input)
if url_match:
    douyin_url = url_match.group(0).rstrip('/')
    print(f'🎯 提取到链接: {douyin_url}')
    print()

    # 配置并处理
    settings = Settings(
        model='small',
        language='zh',
        engine='whisper'
    )
    pipeline = Pipeline(settings)

    print('🚀 开始处理...')
    print()

    try:
        result = pipeline.transcribe(douyin_url)

        print()
        print('✅ 处理完成！')
        print(f'📄 转录文件: {result.transcript_path}')
        print(f'📊 元数据: {result.metadata_path}')
        print()
        print('🎯 前100字预览:')
        if result.transcript_path:
            text = Path(result.transcript_path).read_text(encoding='utf-8')
            print(text[:200] + '...' if len(text) > 200 else text)
    except Exception as e:
        print(f'❌ 处理出错: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
else:
    print('❌ 未找到有效的链接')
