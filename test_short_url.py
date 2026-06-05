"""
测试 Bilibili 短链接解析功能
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from video2text.inputs import parse_source
from video2text.url_utils import extract_bvid, normalize_bilibili_url, resolve_short_url

print("="*70)
print("🔗 Bilibili 短链接解析测试")
print("="*70)
print()

# 测试 URL（您提供的链接）
test_url = "https://b23.tv/5TmfnwG"

print(f"📌 测试 URL: {test_url}")
print()

# 1. 测试 resolve_short_url
print("--- 测试 1: 解析短链接 ---")
real_url = resolve_short_url(test_url)
if real_url:
    print(f"✅ 解析后 URL: {real_url}")
else:
    print("❌ 解析失败")
print()

# 2. 测试 extract_bvid
print("--- 测试 2: 提取 BV 号 ---")
bvid = extract_bvid(real_url or test_url)
if bvid:
    print(f"✅ 提取到 BV 号: {bvid}")
else:
    print("❌ 未找到 BV 号")
print()

# 3. 测试 normalize_bilibili_url
print("--- 测试 3: 规范化 URL ---")
standard_url, bvid = normalize_bilibili_url(test_url)
if standard_url:
    print(f"✅ 标准化 URL: {standard_url}")
    if bvid:
        print(f"✅ 对应的 BV 号: {bvid}")
print()

# 4. 测试 parse_source
print("--- 测试 4: parse_source 整合测试 ---")
source = parse_source(test_url)
print("📦 解析结果:")
print(f"   - raw_input: {source.raw_input}")
print(f"   - kind: {source.kind}")
print(f"   - bv: {source.bv}")
print(f"   - url: {source.url}")
print()

print("="*70)
print("✅ 测试完成！")
print("="*70)
