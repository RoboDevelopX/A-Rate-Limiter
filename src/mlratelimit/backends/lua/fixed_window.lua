-- Fixed window counter. Mirrors mlratelimit/algorithms/fixed_window.py.
-- Hash fields: w = window index, c = units used in that window.
local function evaluate(key, limit, period, cost)
  local window = math.floor(now / period)
  local count = 0
  local stored = redis.call('HMGET', key, 'w', 'c')
  if stored[1] and tonumber(stored[1]) >= window then
    window = tonumber(stored[1])
    count = tonumber(stored[2])
  end
  local reset_at = (window + 1) * period
  local allowed = count + cost <= limit
  if allowed then
    count = count + cost
  end
  local function commit()
    redis.call('HSET', key, 'w', fmt(window), 'c', fmt(count))
    expire(key, reset_at)
  end
  return allowed, limit - count, retry_after_for(allowed, cost, limit, reset_at - now), reset_at, commit
end
