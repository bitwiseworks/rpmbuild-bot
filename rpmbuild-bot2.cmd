/*REXX*/
parse source . . self
parse arg args
 if (translate(right(self, 4)) == '.CMD') then script = left(self, length(self)-4)||'.py'
else script = self||'.py'
script = translate(script, '/', '\')
args   = translate(args, '/', '\')
'@python '''script''' 'args
