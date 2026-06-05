#!/usr/bin/env python3
"""
v3 功能演示 - 不需要浏览器，测试所有非浏览器功能
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from douyin_batch.cache import ProcessCache
from douyin_batch.config import BatchConfig
from douyin_batch.logger import log
from douyin_batch.progress import ProgressTracker, format_duration, format_size

print("=" * 60)
print("🧪 v3 功能演示")
print("=" * 60)

# 1. 日志系统
print("\n1️⃣  日志系统演示")
log.info("普通信息")
log.success("操作成功")
log.warning("警告信息")
log.error("错误信息")
log.progress("正在处理...")

# 2. 配置管理
print("\n2️⃣  配置管理演示")
config = BatchConfig(
    max_videos=20,
    max_retries=5,
    headless=False,
)
config_str = config.__str__()
log.info("配置对象:\n" + config_str)

# 3. 配置保存/加载
print("\n3️⃣  配置序列化演示")
import tempfile

with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    tmp_path = Path(f.name)

config.save(tmp_path)
log.info(f"配置已保存到: {tmp_path}")
log.info(f"文件大小: {format_size(tmp_path.stat().st_size)}")

loaded = BatchConfig.from_file(tmp_path)
assert loaded.max_videos == 20
assert loaded.max_retries == 5
log.success("配置加载并验证成功")
tmp_path.unlink()

# 4. 缓存管理
print("\n4️⃣  缓存管理演示")
import shutil

cache_dir = Path(tempfile.mkdtemp())
cache = ProcessCache(cache_dir=cache_dir)
cache.mark_processed("v1", "url1", success=True)
cache.mark_processed("v2", "url2", success=False)
stats = cache.get_stats()
log.info(f"缓存统计: {stats}")

videos = [
    {"video_id": "v1", "url": "url1"},
    {"video_id": "v2", "url": "url2"},
    {"video_id": "v3", "url": "url3"},
]
unprocessed = cache.filter_unprocessed(videos)
log.info(f"未处理视频: {[v['video_id'] for v in unprocessed]}")
shutil.rmtree(cache_dir)

# 5. 进度条
print("\n5️⃣  进度条演示")
tracker = ProgressTracker(total=10, desc="演示任务")
for i in range(10):
    import time
    time.sleep(0.05)
    tracker.update(success=(i % 3 != 0), task_time=0.5)
    print(tracker.render(f"任务{i+1}"), end="\r")
print()

# 6. 格式化函数
print("\n6️⃣  格式化函数演示")
log.info(f"5秒: {format_duration(5)}")
log.info(f"65秒: {format_duration(65)}")
log.info(f"3661秒: {format_duration(3661)}")
log.info(f"1024字节: {format_size(1024)}")
log.info(f"1MB: {format_size(1024 * 1024)}")

print("\n" + "=" * 60)
log.success("v3 所有功能演示通过！")
print("=" * 60)
