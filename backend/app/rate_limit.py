"""
进程内滑动窗口限流器

用途：会议号枚举防护。6 位会议号空间只有 90 万，若 /join 与会议信息查询
没有任何频率限制，攻击者可以在可接受的时间内遍历全部号码，找出所有有效
会议号（再据此尝试无密码会议或进行定向骚扰）。

部署说明：状态保存在进程内存中，适用于本项目当前的单进程 uvicorn 部署；
若将来改为多进程 / 多实例，需替换为 Redis 等共享存储。
"""
import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
# {限流键: 最近 window 秒内的访问时间戳队列}
_buckets = defaultdict(deque)

# 键数量上限，超过时清理空桶，避免长期运行内存无界增长
_MAX_KEYS = 10000


def hit(key: str, limit: int, window: float = 60.0) -> bool:
    """记一次访问：返回 True 表示放行，False 表示超出配额

    按 key 维护「最近 window 秒内的访问时间戳」，达到 limit 次即拒绝。
    """
    now = time.monotonic()
    with _lock:
        bucket = _buckets[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)

        if len(_buckets) > _MAX_KEYS:
            for stale in [k for k, v in _buckets.items() if not v]:
                del _buckets[stale]
        return True


def forget_prefix(prefix: str) -> None:
    """删除以 prefix 开头的全部计数

    用于连接级限流的收尾：WS 的限流键以连接（会议号 + 参会者）为前缀，
    连接断开后必须删掉，否则参会者记录不断新增会让键无限累积。
    """
    with _lock:
        for key in [k for k in _buckets if k.startswith(prefix)]:
            del _buckets[key]


def reset():
    """清空所有计数（测试夹具使用，避免用例之间互相影响）"""
    with _lock:
        _buckets.clear()