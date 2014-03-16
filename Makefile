
help:
	@echo "run: make venv deps"
	@echo "then ./venv/bin/python demo-client.py create email@restmail.net pw"
	@echo "then ./venv/bin/python demo-client.py verify email@restmail.net pw"
	@echo " (or for non restmail.net acct, click verification email link)"
	@echo " ./venv/bin/python demo-client.py login email@restmail.net pw"
	@echo " ./venv/bin/python demo-client.py login-with-keys email@restmail.net pw"
	@echo " ./venv/bin/python demo-client.py change-password email@restmail.net pw newpw"
	@echo " forgot-password flow:"
	@echo "  ./venv/bin/python demo-client.py forgotpw-send email@restmail.net"
	@echo "  ./venv/bin/python demo-client.py forgotpw-resend email@restmail.net token"
	@echo "  ./venv/bin/python demo-client.py forgotpw-submit email@restmail.net token code newerpw"
	@echo " destroy-account flow:"
	@echo " ./venv/bin/python demo-client.py destroy email@restmail.net newerpw"

venv:
	virtualenv venv

.deps: venv
	venv/bin/pip install scrypt
	venv/bin/pip install requests
	venv/bin/pip install PyHawk
	touch .deps
.PHONY: deps
deps: .deps

vectors: .deps
	venv/bin/python picl-crypto.py
