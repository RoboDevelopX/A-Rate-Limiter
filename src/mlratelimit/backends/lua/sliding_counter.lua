-- Sliding window counter. Mirrors mlratelimit/algorithms/sliding_counter.py.
-- Hash fields: w = current window index, c = its count, p = previous window's count.
local function evaluate(key, limit, period, cost)
  local window = math.floor(now / period)
  local current, previous = 0, 0
  local stored = redis.call('HMGET', key, 'w', 'c', 'p')
  if stored[1] then
    local stored_window = tonumber(stored[1])
    if stored_window >= window then
      window = stored_window
      current = tonumber(stored[2])
      previous = tonumber(stored[3])
    elseif stored_window == window - 1 then
      previous = tonumber(stored[2])
    end
  end
  local start = window * period
  local elapsed = math.min(math.max(now - start, 0), period)
  local weight = (period - elapsed) / period

  local allowed = previous * weight + current + cost <= limit
  if allowed then
    current = current + cost
  end
  local used = previous * weight + current
  local reset_at
  if current > 0 then
    reset_at = start + 2 * period
  elseif previous > 0 then
    reset_at = start + period
  else
    reset_at = now
  end

  local wait = 0
  if not allowed then
    if current + cost <= limit then
      wait = start + period * (1 - (limit - cost - current) / previous) - now
    elseif current > 0 then
      wait = start + period + period * (1 - (limit - cost) / current) - now
    end
  end
  local function commit()
    redis.call('HSET', key, 'w', fmt(window), 'c', fmt(current), 'p', fmt(previous))
    expire(key, start + 2 * period)
  end
  local remaining = math.max(0, math.floor(limit - used))
  return allowed, remaining, retry_after_for(allowed, cost, limit, wait), reset_at, commit
end
