import os, sys
sys.path.insert(0, "/app/src")
from common.redis_client import create_redis_client_with_aad
rd = create_redis_client_with_aad(host=os.environ["REDIS_HOST"], username=os.environ["REDIS_USERNAME"], port=int(os.environ.get("REDIS_PORT","6380")), ssl=True)
print("universe:", rd.get("funding:stats:_universe"))
print("SOL:", rd.get("funding:stats:SOLUSDT"))
keys = list(rd.scan_iter("funding:stats:*", count=200))
print("key count:", len(keys))
