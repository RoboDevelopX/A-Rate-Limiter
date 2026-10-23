-- Leaky bucket as a meter. Mirrors mlratelimit/algorithms/leaky_bucket.py.
-- Hash fields: l = water level, u = time it was measured.
local function evaluate(key, limit, period, cost)
  local rate = limit / period
  local level, updated_at
  local stored = redis.call('HMGET', key, 'l', 'u')
  if stored[1] then
    local stored_at = tonumber(stored[2])
    local elapsed = math.max(0, now - stored_at)
    level = math.max(0, tonumber(stored[1]) - elapsed * rate)
    updated_at = math.max(now, stored_at)
  else
    level = 0
    updated_at = now
  end
  local allowed = level + cost <= limit
  if allowed then
    level = level + cost
  end
  local reset_at = now + level / rate
  local function commit()
    redis.call('HSET', key, 'l', fmt(level), 'u', fmt(updated_at))
    expire(key, reset_at)
  end
  local retry_after = retry_after_for(allowed, cost, limit, (level + cost - limit) / rate)
  return allowed, math.floor(limit - level), retry_after, reset_at, commit
end
