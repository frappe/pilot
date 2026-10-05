def merge($a; $b):
  if ($a|type) == "object" and ($b|type) == "object" then
    reduce (($a + $b) | keys_unsorted[]) as $k ({}; .[$k] = merge($a[$k]; $b[$k]))
  elif ($a|type) == "array" and ($b|type) == "array" then
    reduce ($a + $b)[] as $x ([]; if index([$x]) then . else . + [$x] end)
  elif $b == null then $a
  else $b end;
merge(.[0]; .[1])
