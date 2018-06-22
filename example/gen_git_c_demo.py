"""This demo shows how to generate git credential on local machine."""

from waldorf.cfg import WaldorfCfg
from waldorf.client import WaldorfClient

MASTER_IP = '127.0.0.1'

git_credential = {
    "https://github.com/Seraphli/TestField.git": {
        "Username": "username",
        "Password": "password"
    }
}

cfg = WaldorfCfg(master_ip=MASTER_IP)
client = WaldorfClient(cfg)
encrypted_text = client.generate_git_credential(git_credential)
# Store encrypted information in credential file for later usage.
with open('credential', 'wb') as f:
    f.write(encrypted_text)
