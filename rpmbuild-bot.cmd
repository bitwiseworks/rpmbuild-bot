/**/
parse source . . script
parse arg args
if (right(script, 4) == '.cmd') then script = left(script, length(script)-4)||'.sh'
else script = script||'.sh'
script = translate(script, '/', '\')
if (args \== '') then script = script||' '||args
'@sh -c '''script''''
