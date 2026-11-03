"""Create a new private session key without printing it."""
import os,secrets
from pathlib import Path
os.umask(0o077)
root=Path('.private');root.mkdir(mode=0o700)
fd=os.open(root/'session-key',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o444)
with os.fdopen(fd,'w') as stream:
    stream.write(secrets.token_urlsafe(48));stream.flush();os.fchmod(stream.fileno(),0o444)
print('Private credential created; set KM_SECRET_FILE to absolute .private/session-key path.')
