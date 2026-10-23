-- Sliding window log. Mirrors mlratelimit/algorithms/sliding_log.py.
-- A sorted set scored by timestamp; each member is "<timestamp>:<cost>:<seq>".
local function evaluate(key, limit, period, cost)
  local at = now
  local newest = redis.call('ZRANGE', key, -1, -1, 'WITHSCORES')
  if newest[2] then
    at = math.max(at, tonumber(newest[2]))  -- a clock stepping back must not revive entries
  end
  redis.call('ZREMRANGEBYSCORE', key, '-inf', fmt(at - period))
  local members = redis.call('ZRANGE', key, 0, -1, 'WITHSCORES')
  local times, costs, total = {}, {}, 0
  for i = 1, #members, 2 do
    local entry_cost = tonumber(string.match(members[i], '^[^:]+:(%d+):'))
    times[#times + 1] = tonumber(members[i + 1])
    costs[#costs + 1] = entry_cost
    total = total + entry_cost
  end

  local allowed = total + cost <= limit
  local used = total
  if allowed then
    used = total + cost
  end
  local newest_at = at
  if not allowed and #times > 0 then
    newest_at = times[#times]
  end
  local reset_at = at
  if used > 0 then
    reset_at = newest_at + period
  end

  local wait = 0
  if not allowed and cost <= limit then
    local must_free, freed = total + cost - limit, 0
    for i = 1, #times do
      freed = freed + costs[i]
      if freed >= must_free then
        wait = times[i] + period - at
        break
      end
    end
  end
  local function commit()
    redis.call('ZADD', key, fmt(at), fmt(at) .. ':' .. cost .. ':' .. #times)
    expire(key, reset_at)
  end
  return allowed, limit - used, retry_after_for(allowed, cost, limit, wait), reset_at, commit
end
