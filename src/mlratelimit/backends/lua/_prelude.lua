-- Shared prelude for every rate limit script. The Python backend concatenates
-- _prelude.lua + <algorithm>.lua + _driver.lua into one script.
--
-- ARGV[1]  "" to use Redis server time, or a fixed Unix time (tests only)
-- ARGV[2]  seconds to keep keys past their logical expiry
-- ARGV[3 + 3*(i-1) .. 5 + 3*(i-1)]  limit, period, cost for KEYS[i]

local now
if ARGV[1] ~= '' then
  now = tonumber(ARGV[1])
else
  local t = redis.call('TIME')
  now = tonumber(t[1]) + tonumber(t[2]) / 1000000
end
local ttl_padding = tonumber(ARGV[2])

-- Redis turns Lua numbers into integers in replies and into 14-digit strings in
-- commands, so every float crosses the boundary as a full-precision string.
local function fmt(x)
  return string.format('%.17g', x)
end

local function expire(key, expires_at)
  redis.call('PEXPIRE', key, math.max(1, math.ceil((expires_at - now + ttl_padding) * 1000)))
end

local function retry_after_for(allowed, cost, limit, wait)
  if allowed then
    return '0'
  end
  if cost > limit then
    return ''
  end
  return fmt(math.max(0, wait))
end
