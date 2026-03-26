import asyncio
from app.sources.stats import build_stats_cache

if __name__ == "__main__":
    asyncio.run(build_stats_cache())