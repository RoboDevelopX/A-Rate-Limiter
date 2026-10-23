-- Token bucket. Mirrors mlratelimit/algorithms/token_bucket.py.
-- Hash fields: t = tokens left, u = time they were counted.
local function evaluate(key, limit, period, cost)
  local rate = limit / period
  local tokens, updated_at
  local stored = redis.call('HMGET', key, 't', 'u')
  if stored[1] then
    local stored_at = tonumber(stored[2])
    local elapsed = math.max(0, now - stored_at)
    tokens = math.min(limit, tonumber(stored[1]) + elapsed * rate)
    updated_at = math.max(now, stored_at)
  else
    tokens = limit
    updated_at = now
  end
  local allowed = tokens >= cost
  if allowed then
    tokens = tokens - cost
  end
  local reset_at = now + (limit - tokens) / rate
  local function commit()
    redis.call('HSET', key, 't', fmt(tokens), 'u', fmt(updated_at))
    expire(key, reset_at)
  end
  local retry_after = retry_after_for(allowed, cost, limit, (cost - tokens) / rate)
  return allowed, math.floor(tokens), retry_after, reset_at, commit
end
