-- Evaluate every key first, then write only if all of them are allowed.
local reply, commits, all_allowed = {}, {}, true
for i, key in ipairs(KEYS) do
  local base = 3 * i
  local limit = tonumber(ARGV[base])
  local period = tonumber(ARGV[base + 1])
  local cost = tonumber(ARGV[base + 2])
  local allowed, remaining, retry_after, reset_at, commit = evaluate(key, limit, period, cost)
  if not allowed then
    all_allowed = false
  end
  commits[i] = commit
  reply[#reply + 1] = allowed and 1 or 0
  reply[#reply + 1] = remaining
  reply[#reply + 1] = retry_after
  reply[#reply + 1] = fmt(reset_at)
end
if all_allowed then
  for i = 1, #commits do
    commits[i]()
  end
end
return reply
