/*REXX*/
parse source . . script
parse arg args
if (translate(right(script, 4)) == '.CMD') then script = left(script, length(script)-4)||'.sh'
else script = script||'.sh'
script = translate(script, '/', '\')
args   = translate(args, '/', '\')
if (args \== '') then script = script||' '||args
'@sh -c '''script''''
